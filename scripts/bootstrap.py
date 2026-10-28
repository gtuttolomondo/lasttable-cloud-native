"""Run versioned SQL/Kafka initialization and live checks in the private OKE network."""
import argparse
import base64
import json
import subprocess
import uuid
from oci._vendor import requests
import oci
import cloud

NS='lasttable-bootstrap'
KUBE=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]


def apply(name,objects):
    path=cloud.save(name,{'apiVersion':'v1','kind':'List','items':objects})
    subprocess.run(KUBE+['apply','-f',str(path)],check=True)


def run(image,mode):
    assert '@sha256:' in image,'Use an authoritative, tested bootstrap image digest'
    cfg=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
    host=image.split('/')[0]
    assert host.endswith('.ocir.io')
    signer=oci.signer.Signer(cloud.config['tenancy'],cloud.config['user'],cloud.config['fingerprint'],cloud.config['key_file'],pass_phrase=cloud.config.get('pass_phrase'))
    response=requests.get('https://'+host+'/20180419/docker/token',auth=signer,timeout=20)
    response.raise_for_status()
    auth=base64.b64encode(('BEARER_TOKEN:'+response.json()['token']).encode()).decode()
    encoded=base64.b64encode(json.dumps({'auths':{host:{'auth':auth}}}).encode()).decode()
    def meta(name):return {'name':name,'namespace':NS}
    apply('bootstrap-foundation',[
        {'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS}},
        {'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('bootstrap')},
        {'apiVersion':'v1','kind':'ConfigMap','metadata':meta('bootstrap-config'),'data':{'config.json':json.dumps(cfg)}},
        {'apiVersion':'v1','kind':'Secret','metadata':meta('ocir-pull'),'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':encoded}},
    ])
    name='verify-data-'+uuid.uuid4().hex[:8]
    args=[] if mode=='all' else ['--'+mode+'-only']
    job={'apiVersion':'batch/v1','kind':'Job','metadata':meta(name),'spec':{
        'backoffLimit':0,'activeDeadlineSeconds':420,'template':{'spec':{
            'restartPolicy':'Never','serviceAccountName':'bootstrap','imagePullSecrets':[{'name':'ocir-pull'}],
            'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'seccompProfile':{'type':'RuntimeDefault'}},
            'containers':[{'name':'bootstrap','image':image,'args':args,
                'env':[{'name':'BOOTSTRAP_CONFIG_PATH','value':'/config/config.json'}],
                'volumeMounts':[{'name':'config','mountPath':'/config','readOnly':True}],
                'resources':{'requests':{'cpu':'250m','memory':'256Mi'},'limits':{'cpu':'1000m','memory':'768Mi'}},
                'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}}}],
            'volumes':[{'name':'config','configMap':{'name':'bootstrap-config'}}],
        }}}}
    apply('bootstrap-job',[job])
    print('Bootstrap job created:',name,'; inspect logs and wait for Complete before cleanup')
    cloud.save('bootstrap-last-job',{'name':name,'namespace':NS,'mode':mode})


def verify():
    job=json.loads((cloud.PRIVATE/'bootstrap-last-job.json').read_text())
    state=subprocess.check_output(KUBE+['get','job',job['name'],'-n',NS,'-o','json'],text=True)
    data=json.loads(state)
    result=subprocess.run(KUBE+['logs','job/'+job['name'],'-n',NS],capture_output=True,text=True)
    path=cloud.PRIVATE/'bootstrap-verification.log';path.write_text(result.stdout+result.stderr);path.chmod(0o600)
    print(result.stdout)
    assert data.get('status',{}).get('succeeded',0)==1,'Bootstrap has not completed successfully; inspect private job evidence'
    print('PASS: private bootstrap job Complete')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','verify','cleanup']);p.add_argument('--image');p.add_argument('--mode',choices=['all','database','kafka'],default='all');a=p.parse_args()
    if a.action=='run':run(a.image,a.mode)
    elif a.action=='verify':verify()
    else:subprocess.run(KUBE+['delete','namespace',NS,'--ignore-not-found'],check=True)
