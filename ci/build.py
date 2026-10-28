"""Build/test locally on the runner; native OCI Deliver Artifacts publishes images."""
import json
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
exports={'RELEASE':release}

def run(*args):subprocess.run(args,check=True)

for role,source in [('backend','app'),('frontend','frontend'),('notifications','notifications')]:
    image=host+'/'+namespace+'/'+project+'/'+role+':'+release
    run(engine,'build','--network','host','--platform','linux/amd64','-f',source+'/Containerfile','-t',image,'.')
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
    exports[role.upper()+'_IMAGE_TAG']=image
Path('.release').mkdir(exist_ok=True)
Path('.release/release.json').write_text(json.dumps(exports,indent=2)+'\n')
Path('.release/export.sh').write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in exports.items()))
print(json.dumps({'release':release,'images_built_and_tested':3,'publication':'native Deliver Artifacts stage'}))
