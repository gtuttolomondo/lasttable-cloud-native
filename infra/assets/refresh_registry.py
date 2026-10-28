"""Mint a short-lived OCIR pull credential with OKE Workload Identity.

Runs from Oracle's public OCI CLI image, so an expired private-registry token
cannot prevent its own recovery. RBAC permits patching only ocir-pull.
"""
import base64
import json
import os
from pathlib import Path
import oci
from oci._vendor import requests

host=os.environ['REGISTRY_HOST']
assert host.endswith('.ocir.io') and '/' not in host
signer=oci.auth.signers.get_oke_workload_identity_resource_principal_signer()
response=requests.get('https://'+host+'/20180419/docker/token',auth=signer,timeout=20)
response.raise_for_status()
credential=response.json()
assert credential.get('token')
auth=base64.b64encode(('BEARER_TOKEN:'+credential['token']).encode()).decode()
docker={'auths':{host:{'auth':auth}}}
data={'.dockerconfigjson':base64.b64encode(json.dumps(docker).encode()).decode()}
mount=Path('/var/run/secrets/kubernetes.io/serviceaccount')
token=(mount/'token').read_text().strip()
namespace=(mount/'namespace').read_text().strip()
server='https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ['KUBERNETES_SERVICE_PORT_HTTPS']
patched=requests.patch(server+'/api/v1/namespaces/'+namespace+'/secrets/ocir-pull',
    json={'data':data},headers={'Authorization':'Bearer '+token,'Content-Type':'application/merge-patch+json'},
    verify=str(mount/'ca.crt'),timeout=15)
patched.raise_for_status()
print(json.dumps({'registry_pull_credential':'refreshed','expires_in':credential.get('expires_in'),'namespace':namespace}),flush=True)
