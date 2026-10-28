"""Install operator foundations; deployment pipeline never receives secret/RBAC management."""
import json
import subprocess
import yaml
import cloud

state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
objects=yaml.safe_load(state['outputs']['kubernetes_foundation']['value'])['items']
namespace='lasttable'
kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]
# Confirm API connectivity before interpreting NotFound as an absent secret.
subprocess.run(kube+['version','--request-timeout=20s'],check=True,capture_output=True)
secret=subprocess.run(kube+['get','secret','ocir-pull','-n',namespace],capture_output=True,text=True)
if secret.returncode:
    assert 'NotFound' in secret.stderr,'Cannot determine existing pull credential; refusing replacement'
    objects.insert(1,{'apiVersion':'v1','kind':'Secret','metadata':{'name':'ocir-pull','namespace':namespace},
        'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':'eyJhdXRocyI6e319'}})
path=cloud.save('registry-foundation',{'apiVersion':'v1','kind':'List','items':objects})
subprocess.run(kube+['apply','-f',str(path)],check=True)
identity=state['outputs']['devops']['value']['deploy_pipeline_id']
checks=[('lasttable','create','deployments','yes'),('default','create','deployments','no'),
    ('lasttable','get','secrets','no'),('lasttable','create','rolebindings','no')]
for ns,verb,resource,expected in checks:
    result=subprocess.run(kube+['auth','can-i',verb,resource,'--as='+identity,'--as-group=system:authenticated','-n',ns],capture_output=True,text=True)
    assert result.stdout.strip().split(maxsplit=1)[0]==expected,'Unexpected release RBAC boundary'
cloud.save('deployment-rbac-verification',{'application_deployments':'allowed','other_namespace':'denied','secrets':'denied','rbac_changes':'denied','cluster_admin':'not_granted'})
print('Operator foundation installed; deployment identity limited to application objects')
