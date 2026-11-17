"""Build/test locally on the runner; native OCI Deliver Artifacts publishes images."""
import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

engine=shutil.which('podman') or shutil.which('docker')
assert engine,'A container engine is required'
label=os.environ.get('RELEASE_LABEL','v1')
assert re.fullmatch(r'[a-zA-Z0-9_.-]{1,24}',label)
release=label+'-'+os.environ['OCI_BUILD_RUN_ID'].split('.')[-1][-12:]
host=os.environ['REGISTRY_HOST'];namespace=os.environ['REGISTRY_NAMESPACE'];project=os.environ['PROJECT_NAME']
assert re.fullmatch(r'[a-z0-9.-]+\.ocir\.io',host)
probe=os.environ.get('CI_PROBE_ONLY','false').lower()=='true'
production=os.environ.get('CI_PRODUCTION_DEPLOY_ENABLED','false').lower()=='true'
assert not (probe and production),'Probe images cannot enter a production-enabled pipeline'
if probe:release='probe-'+release
commit=os.environ.get('OCI_PRIMARY_SOURCE_COMMIT_HASH') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
baseline=json.loads(Path('ci/application-baseline.json').read_text())
for path,digest in baseline['files'].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest,'Application differs from approved baseline: '+path
manifest_hash=hashlib.sha256(json.dumps(baseline['files'],sort_keys=True).encode()).hexdigest()
exports={'RELEASE':release}
provenance={'source_commit':commit,'build_run_id':os.environ['OCI_BUILD_RUN_ID'],'baseline_commit':baseline['reference_commit'],'application_manifest_sha256':manifest_hash,'probe_only':probe}


def run(*args):subprocess.run(args,check=True)

for role,source in [('backend','app'),('frontend','frontend'),('notifications','notifications')]:
    image='localhost/lasttable-'+role+':ci'
    recipe='ci/probe/Containerfile' if probe else source+'/Containerfile'
    run(engine,'build','--network','host','--platform','linux/amd64','--label','org.opencontainers.image.revision='+commit,'--label','io.lasttable.application-manifest='+manifest_hash,'-f',recipe,'-t',image,'.')
    if probe:
        exports[role.upper()+'_IMAGE_TAG']=host+'/'+namespace+'/'+project+'/'+role+':'+release
        continue
    if role in ('backend','notifications'):
        frozen=subprocess.check_output([engine,'run','--rm','--entrypoint','python',image,'-m','pip','freeze','--all'],text=True).splitlines()
        assert sorted(frozen)==sorted(baseline['python_packages'][role]),'Runtime dependencies differ from released baseline: '+role
    if role=='frontend':
        hashes=subprocess.check_output([engine,'run','--rm','--entrypoint','sh',image,'-c','find /srv -type f -exec sha256sum {} +'],text=True)
        actual={line.split(None,1)[1].removeprefix('/srv/'):line.split(None,1)[0] for line in hashes.splitlines()}
        assert actual==baseline['frontend_assets'],'Frontend assets differ from released baseline'

    if role=='frontend':
        # Mirror the pod's capability boundary: inherited file capabilities
        # otherwise make exec fail before Caddy can bind its unprivileged ports.
        run(engine,'run','--rm','--cap-drop','ALL','--security-opt','no-new-privileges',image,'caddy','version')
    if role=='backend':
        name='lasttable-contract-tests'
        run(engine,'create','--name',name,'--entrypoint','python',image,'-m','unittest','discover','-s','/tests','-p','test_*.py')
        try:
            run(engine,'cp','app/tests/.',name+':/tests')
            run(engine,'start','--attach',name)
            assert subprocess.check_output([engine,'inspect','--format','{{.State.ExitCode}}',name],text=True).strip()=='0'
        finally:subprocess.run([engine,'rm','-f',name],check=False,stdout=subprocess.DEVNULL)
    if role=='notifications':
        name='lasttable-fdk-test'
        run(engine,'run','-d','--name',name,'-e','FN_FORMAT=http-stream','-e','FN_LISTENER=unix:/tmp/fn.sock',image)
        try:
            run(engine,'cp','app/tests/fdk_probe.py',name+':/tmp/fdk_probe.py')
            run(engine,'exec',name,'python','/tmp/fdk_probe.py')
        finally:subprocess.run([engine,'rm','-f',name],check=False,stdout=subprocess.DEVNULL)
    exports[role.upper()+'_IMAGE_TAG']=host+'/'+namespace+'/'+project+'/'+role+':'+release
Path('.release').mkdir(exist_ok=True)
Path('.release/release.json').write_text(json.dumps({**exports,**provenance},indent=2)+'\n')
Path('.release/export.sh').write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in exports.items()))
print(json.dumps({'release':release,**provenance,'mode':'probe' if probe else 'application','images_built':3,'images_tested':0 if probe else 3,'publication':'native Deliver Artifacts stage'}))
