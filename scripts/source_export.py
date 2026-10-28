"""Export only reviewed source files to the temporary private OCI repository.

Private environment files, diagrams/decks, generated outputs and build caches are
excluded by an explicit allowlist, independently of Git ignore configuration.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
import cloud

BRANCH='codex/implementation'
from source_inventory import files, check


def export():
    target=cloud.PRIVATE/'oci-source'
    target.mkdir(exist_ok=True)
    for child in target.iterdir():
        if child.name=='.git':continue
        if child.is_dir():shutil.rmtree(child)
        else:child.unlink()
    manifest={}
    import hashlib
    for file in files(cloud.ROOT):
        relative=file.relative_to(cloud.ROOT)
        assert not set(relative.parts)&{'.local','.tools','.git'}
        content=file.read_text()
        check(content)
        destination=target/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(file,destination)
        manifest[str(relative)]=hashlib.sha256(file.read_bytes()).hexdigest()
    cloud.save('source-export-manifest',manifest)
    print(json.dumps({'exported_source_files':len(manifest),'private_files':'excluded','secret_scan':'passed'}))
    return target


def push(message):
    target=export()
    identity=json.loads((cloud.PRIVATE/'scm-identity.json').read_text())
    outputs=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())['outputs'].get('devops',{}).get('value')
    if not outputs:
        repository=json.loads((cloud.PRIVATE/'source-repository.json').read_text())
        outputs={'repository_id':repository['id'],'repository_ssh_url':repository['ssh_url']}
    url=outputs['repository_ssh_url']
    assert identity['host'] in url,'Unexpected repository destination'
    ssh=['ssh','-i',identity['key'],'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
         '-o','UserKnownHostsFile="'+identity['known_hosts']+'"','-l',identity['username']]
    env={**os.environ,'GIT_SSH_COMMAND':shlex.join(ssh),'GIT_TERMINAL_PROMPT':'0'}
    def git(*args,check=True):return subprocess.run(['git',*args],cwd=target,env=env,check=check,capture_output=True,text=True)
    if not (target/'.git').exists():
        git('init','-b',BRANCH)
        git('config','user.name','LastTable Demo')
        git('config','user.email','demo@example.invalid')
        git('remote','add','oci',url)
    git('add','--all')
    if git('diff','--cached','--quiet',check=False).returncode:
        git('commit','-m',message)
    fetched=git('fetch','oci',BRANCH,check=False)
    if fetched.returncode==0:
        # OCI can initialize the hosted repository with its own first commit.
        # Preserve that history instead of force-pushing an unrelated root.
        git('merge','--no-edit','--allow-unrelated-histories','oci/'+BRANCH)
    result=git('push','-u','oci',BRANCH,check=False)
    cloud.save('scm-push-result',{'code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    assert result.returncode==0,'Private OCI push failed; private diagnostic saved'
    commit=git('rev-parse','HEAD').stdout.strip()
    cloud.save('source-release',{'commit':commit,'branch':BRANCH,'repository_id':outputs['repository_id']})
    print(json.dumps({'private_oci_source':'pushed','commit':commit,'branch':BRANCH,'public_github':'not_published'}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['export','push']);p.add_argument('--message',default='Implement LastTable OCI event-driven demo');args=p.parse_args()
    if args.action=='export':export()
    else:push(args.message)
