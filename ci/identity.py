"""Build identity and cancelled-upload authorization check; never print tokens or keys."""
import json
import base64
import os
import re
import oci
from urllib.parse import urljoin, urlparse
from oci._vendor import requests

signer=oci.auth.signers.get_resource_principals_signer()
allowed=['sub','res_id','res_type','res_compartment','res_grp','iat','exp']
metadata={key:signer.get_claim(key) for key in allowed if hasattr(signer,'get_claim')}
print(json.dumps({'identity_metadata':metadata}),flush=True)
region=os.environ.get('OCI_RESOURCE_PRINCIPAL_REGION') or signer.region
api=oci.artifacts.ArtifactsClient({'region':region},signer=signer)
repositories=oci.pagination.list_call_get_all_results(api.list_container_repositories,compartment_id=os.environ['COMPARTMENT_OCID']).data
selected=[r for r in repositories if r.display_name in [os.environ['PROJECT_NAME']+'/'+role for role in ['backend','frontend','notifications']]]
assert len(selected)==3 and all(not r.is_public for r in selected)
print(json.dumps({'private_repository_read':'passed','compartment_matches':all(r.compartment_id==os.environ['COMPARTMENT_OCID'] for r in selected),'repositories':[r.display_name for r in selected]}),flush=True)

# Keep the image metadata unchanged while testing the exact UPDATE permission
# through the OCI API independently of Docker's token exchange.
images=oci.pagination.list_call_get_all_results(api.list_container_images,
    compartment_id=os.environ['COMPARTMENT_OCID'],
    repository_name=os.environ['PROJECT_NAME']+'/backend').data
assert images, 'A tested seed image must exist before managed CI'
image=api.get_container_image(images[0].id).data
try:
    api.update_container_image(image.id,oci.artifacts.models.UpdateContainerImageDetails(
        freeform_tags=image.freeform_tags))
    print(json.dumps({'image_metadata_update_unchanged':'passed'}),flush=True)
except oci.exceptions.ServiceError as error:
    print(json.dumps({'image_metadata_update_unchanged':error.status,
        'code':error.code,'request_id':error.request_id}),flush=True)

# Check the actual Docker write boundary before spending time building images.
# An empty upload is immediately cancelled; no image or tag is created.
host=os.environ['REGISTRY_HOST']
assert host.endswith('.ocir.io') and '/' not in host
response=requests.get('https://'+host+'/20180419/docker/token',auth=signer,timeout=30)
response.raise_for_status()
token=response.json()['token']
repository=os.environ['REGISTRY_NAMESPACE']+'/'+os.environ['PROJECT_NAME']+'/backend'
auth=('BEARER_TOKEN',token)
challenge=requests.get('https://'+host+'/v2/',timeout=30).headers.get('WWW-Authenticate','')
parts=dict(re.findall(r'(\w+)="([^"]+)"',challenge))
assert urlparse(parts['realm']).netloc==host
session=requests.get(parts['realm'],params={'service':parts.get('service',host),
    'scope':'repository:'+repository+':pull,push'},auth=auth,timeout=30)
session.raise_for_status()
registry_token=session.json()['token']
if registry_token.count('.')==2:
    segment=registry_token.split('.')[1]
    claims=json.loads(base64.urlsafe_b64decode(segment+'='*(-len(segment)%4)))
    print(json.dumps({'registry_access_scope':claims.get('access',claims.get('scope'))}),flush=True)
headers={'Authorization':'Bearer '+registry_token}
upload=requests.post('https://'+host+'/v2/'+repository+'/blobs/uploads/',headers=headers,timeout=30)
print(json.dumps({'registry_upload_preflight':upload.status_code,
    'request_id':upload.headers.get('opc-request-id')}),flush=True)
if upload.status_code==202:
    location=urljoin('https://'+host,upload.headers['Location'])
    assert urlparse(location).netloc==host
    cancel=requests.delete(location,headers=headers,timeout=30)
    assert cancel.status_code in (202,204), 'Empty upload cancellation failed'
else:
    # Compare the documented full regional alias without changing repository,
    # identity or IAM permissions. Never print either bearer token.
    alternate=region+'.ocir.io'
    if alternate!=host:
        response=requests.get('https://'+alternate+'/20180419/docker/token',auth=signer,timeout=30)
        response.raise_for_status()
        challenge=requests.get('https://'+alternate+'/v2/',timeout=30).headers.get('WWW-Authenticate','')
        parts=dict(re.findall(r'(\w+)="([^"]+)"',challenge))
        assert urlparse(parts['realm']).netloc==alternate
        session=requests.get(parts['realm'],params={'service':parts.get('service',alternate),
            'scope':'repository:'+repository+':pull,push'},
            auth=('BEARER_TOKEN',response.json()['token']),timeout=30)
        session.raise_for_status()
        alternate_headers={'Authorization':'Bearer '+session.json()['token']}
        result=requests.post('https://'+alternate+'/v2/'+repository+'/blobs/uploads/',
            headers=alternate_headers,timeout=30)
        print(json.dumps({'regional_alias':alternate,'registry_upload_preflight':result.status_code,
            'request_id':result.headers.get('opc-request-id')}),flush=True)
        if result.status_code==202:
            location=urljoin('https://'+alternate,result.headers['Location'])
            assert urlparse(location).netloc==alternate
            cancelled=requests.delete(location,headers=alternate_headers,timeout=30)
            assert cancelled.status_code in (202,204)
            raise RuntimeError('Regional alias permits upload; configure REGISTRY_HOST before building')
    raise RuntimeError('Registry write authorization failed before image build')
