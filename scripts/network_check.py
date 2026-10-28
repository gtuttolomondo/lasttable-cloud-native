"""Prove private image pull, cluster DNS and OCI HTTPS from both OKE nodes."""
import argparse
import base64
import json
import subprocess
import yaml
import cloud

NS='lasttable-verification'
KUBE=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]


def run(image):
    assert '@sha256:' in image,'Use an authoritative private OCIR image digest'
    objects=list(yaml.safe_load_all((cloud.ROOT/'tests/kubernetes/network-smoke.yaml').read_text()))
    pod=objects[1]['spec']['template']['spec']
    pod['imagePullSecrets']=[{'name':'ocir-pull'}]
    pod['containers'][0]['image']=image
    host=image.split('/')[0]
    command=pod['containers'][0]['command'][2]
    pod['containers'][0]['command'][2]=command.replace('lin.ocir.io',host).replace('eu-milan-1',cloud.config['region'])
    auth=(cloud.PRIVATE/'registry-auth.json').read_bytes()
    objects.insert(1,{'apiVersion':'v1','kind':'Secret','metadata':{'name':'ocir-pull','namespace':NS},
        'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':base64.b64encode(auth).decode()}})
    path=cloud.save('network-check-job',{'apiVersion':'v1','kind':'List','items':objects})
    subprocess.run(KUBE+['apply','-f',str(path)],check=True)


def verify():
    job=json.loads(subprocess.check_output(KUBE+['get','job','network-smoke','-n',NS,'-o','json'],text=True))
    assert job.get('status',{}).get('succeeded',0)==2,'Both probe pods must complete'
    pods=json.loads(subprocess.check_output(KUBE+['get','pods','-n',NS,'-l','app=lasttable-network-smoke','-o','json'],text=True))['items']
    nodes=set()
    for pod in pods:
        assert pod['status']['phase']=='Succeeded'
        nodes.add(pod['spec']['nodeName'])
        output=subprocess.check_output(KUBE+['logs',pod['metadata']['name'],'-n',NS],text=True)
        assert 'NETWORK_SMOKE_PASSED' in output
        path=cloud.PRIVATE/('network-check-'+pod['metadata']['name']+'.log');path.write_text(output);path.chmod(0o600)
    assert len(nodes)==2,'Probe must run on both nodes'
    result={'private_image_pull':'passed','cluster_dns':'passed','ocir_and_vault_https':'passed','nodes':sorted(nodes)}
    cloud.save('network-runtime-verification',result)
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','verify','cleanup']);p.add_argument('--image');a=p.parse_args()
    if a.action=='run':run(a.image)
    elif a.action=='verify':verify()
    else:subprocess.run(KUBE+['delete','namespace',NS,'--ignore-not-found'],check=True)
