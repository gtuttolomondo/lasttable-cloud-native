"""Apply database migration 2 in a temporary job on the existing OKE cluster."""
import base64
import json
import subprocess
import uuid
from oci._vendor import requests
import oci
import cloud

kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]
ns='lasttable-bootstrap'
config=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
image=json.loads((cloud.PRIVATE/'published-bootstrap.json').read_text())['digest_ref']
host=image.split('/')[0]
signer=oci.signer.Signer(cloud.config['tenancy'],cloud.config['user'],cloud.config['fingerprint'],cloud.config['key_file'],pass_phrase=cloud.config.get('pass_phrase'))
response=requests.get('https://'+host+'/20180419/docker/token',auth=signer,timeout=20)
response.raise_for_status()
auth=base64.b64encode(('BEARER_TOKEN:'+response.json()['token']).encode()).decode()
encoded=base64.b64encode(json.dumps({'auths':{host:{'auth':auth}}}).encode()).decode()
name='manual-admin-migration-'+uuid.uuid4().hex[:8]
def meta(name):return {'name':name,'namespace':ns}
items=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns}},
 {'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('bootstrap')},
 {'apiVersion':'v1','kind':'Secret','metadata':meta('ocir-pull'),'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':encoded}},
 {'apiVersion':'v1','kind':'ConfigMap','metadata':meta('admin-migration-config'),'data':{'config.json':json.dumps(config)}},
 {'apiVersion':'v1','kind':'ConfigMap','metadata':meta('admin-migration-code'),'data':{n:(cloud.ROOT/'bootstrap'/n).read_text() for n in ['migrate_admin.py','verify_data.py']}},
 {'apiVersion':'batch/v1','kind':'Job','metadata':meta(name),'spec':{'ttlSecondsAfterFinished':1800,'backoffLimit':0,'activeDeadlineSeconds':240,
  'template':{'spec':{'restartPolicy':'Never','serviceAccountName':'bootstrap','imagePullSecrets':[{'name':'ocir-pull'}],
   'securityContext':{'runAsNonRoot':True,'runAsUser':65532},
   'containers':[{'name':'migration','image':image,'command':['python','/migration/migrate_admin.py'],
    'env':[{'name':'BOOTSTRAP_CONFIG_PATH','value':'/config/config.json'}],
    'volumeMounts':[{'name':'config','mountPath':'/config','readOnly':True},{'name':'code','mountPath':'/migration','readOnly':True}],
    'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'500m','memory':'512Mi'}}}],
   'volumes':[{'name':'config','configMap':{'name':'admin-migration-config'}},{'name':'code','configMap':{'name':'admin-migration-code'}}]}}}}]
subprocess.run(kube+['apply','-f','-'],input=json.dumps({'apiVersion':'v1','kind':'List','items':items}),text=True,check=True,capture_output=True)
cloud.save('admin-migration-job',{'name':name,'namespace':ns})
print('Migration job created:',name)
