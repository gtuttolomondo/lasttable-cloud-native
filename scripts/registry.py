"""OCIR access with short-lived, OCI-signed credentials; no user auth-token changes."""
import argparse
import json
import subprocess
import re
import time
from datetime import datetime, timezone
from oci._vendor import requests
import oci
import cloud

def login():
    host=json.loads((cloud.PRIVATE/'registry-endpoint.json').read_text())['host']
    assert host.endswith('.ocir.io') and '/' not in host
    signer=oci.signer.Signer(cloud.config['tenancy'],cloud.config['user'],cloud.config['fingerprint'],
        cloud.config['key_file'],pass_phrase=cloud.config.get('pass_phrase'))
    response=requests.get(f'https://{host}/20180419/docker/token',auth=signer,timeout=20)
    response.raise_for_status()
    data=response.json();assert data.get('token')
    cloud.save('registry-session',data)
    authfile=cloud.PRIVATE/'registry-auth.json'
    r=subprocess.run(['podman','login','--authfile',str(authfile),'--username','BEARER_TOKEN',
        '--password-stdin',host],input=data['token'],text=True,capture_output=True)
    if r.returncode:
        cloud.save('registry-login-error',{'stdout':r.stdout,'stderr':r.stderr})
        raise RuntimeError('Registry login failed; private diagnostic saved without printing credentials')
    authfile.chmod(0o600)
    print('Temporary Podman/OCIR login succeeded')
    return host,authfile

def smoke():
    host,authfile=login()
    namespace=json.loads((cloud.PRIVATE/'preflight.json').read_text())['ocir_namespace']
    tag='smoke-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
    repository=variables.get('project_name','lasttable')+'/backend'
    image=f'{host}/{namespace}/{repository}:{tag}'
    cached=subprocess.run(['podman','image','inspect','docker.io/library/alpine:3.22','--format','{{.Architecture}}'],capture_output=True,text=True)
    if cached.returncode or cached.stdout.strip()!='amd64':
        subprocess.run(['podman','pull','--platform','linux/amd64','docker.io/library/alpine:3.22'],check=True)
    for args in [
        ['podman','tag','docker.io/library/alpine:3.22',image],
        ['podman','push','--authfile',str(authfile),image],
        ['podman','pull','--authfile',str(authfile),'--platform','linux/amd64',image],
        ['podman','run','--rm','--platform','linux/amd64',image,'sh','-c','echo ocir-smoke-ok; uname -m'],
    ]: subprocess.run(args,check=True)
    # Local stores can retain digest aliases from converted manifests. Use OCIR's
    # authoritative digest rather than selecting the first local RepoDigest.
    images=cloud.client(oci.artifacts.ArtifactsClient).list_container_images(
        compartment_id=cloud.COMPARTMENT,repository_name=repository).data.items
    actual=next(x for x in images if x.display_name==f'{repository}:{tag}')
    digest=f'{host}/{namespace}/{repository}@{actual.digest}'
    cloud.save('registry-smoke',{'image':image,'digest_ref':digest,'push':'passed','pull':'passed','local_execution':'passed'})
    print('OCIR push, pull and image execution passed')

def publish(local_image,repository,tag,record):
    assert repository in ['backend','frontend','notifications']
    assert re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',tag)
    assert re.fullmatch(r'[a-z0-9-]{1,40}',record)
    host,authfile=login()
    namespace=json.loads((cloud.PRIVATE/'preflight.json').read_text())['ocir_namespace']
    variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
    repository=variables.get('project_name','lasttable')+'/'+repository
    artifacts=cloud.client(oci.artifacts.ArtifactsClient)
    repositories=artifacts.list_container_repositories(compartment_id=cloud.COMPARTMENT).data.items
    assert any(r.display_name==repository and not r.is_public for r in repositories), 'Expected a private Terraform-owned repository in the demo compartment'
    existing=artifacts.list_container_images(compartment_id=cloud.COMPARTMENT,repository_name=repository).data.items
    assert not any(x.display_name==repository+':'+tag for x in existing), 'Release tag already exists; select a new tag'
    image=f'{host}/{namespace}/{repository}:{tag}'
    subprocess.run(['podman','tag',local_image,image],check=True)
    subprocess.run(['podman','push','--authfile',str(authfile),image],check=True)
    actual=None
    for _ in range(10):
        images=artifacts.list_container_images(compartment_id=cloud.COMPARTMENT,repository_name=repository).data.items
        actual=next((x for x in images if x.display_name==repository+':'+tag),None)
        if actual:break
        time.sleep(1)
    assert actual, 'Published image metadata not yet available'
    metadata={'image':image,'digest_ref':f'{host}/{namespace}/{repository}@{actual.digest}'}
    cloud.save('published-'+record,metadata)
    print(json.dumps(metadata))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['login','smoke','publish'])
    p.add_argument('--local-image');p.add_argument('--repository');p.add_argument('--tag');p.add_argument('--record',default='release');args=p.parse_args()
    if args.action=='publish':publish(args.local_image,args.repository,args.tag,args.record)
    else:{'login':login,'smoke':smoke}[args.action]()
