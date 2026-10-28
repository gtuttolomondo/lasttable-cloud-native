"""Configure the manual-admin release on the existing cluster and deploy artifact.

The access code is read from .local/table-admin-code.json and is never included
in source, manifests, release images or printed command output. Terraform
consolidation of this development overlay is a later, explicit release step.
"""
import base64
import json
import subprocess
import oci
import yaml
import cloud

KUBE=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]


def configure():
    code=json.loads((cloud.PRIVATE/'table-admin-code.json').read_text())['code']
    assert isinstance(code,str) and len(code)>=6
    obj={'apiVersion':'v1','kind':'Secret','metadata':{'name':'table-admin-access','namespace':'lasttable'},
         'type':'Opaque','stringData':{'code':code}}
    subprocess.run(KUBE+['apply','-f','-'],input=json.dumps(obj),text=True,check=True,capture_output=True)
    state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
    identifier=next(r['instances'][0]['attributes']['id'] for r in state['resources']
        if r['type']=='oci_devops_deploy_artifact' and r['name']=='manifest')
    api=cloud.client(oci.devops.DevopsClient)
    artifact=api.get_deploy_artifact(identifier).data

    content=base64.b64decode(artifact.deploy_artifact_source.base64_encoded_content).decode()
    documents=list(yaml.safe_load_all(content))
    changed=False
    for doc in documents:
        objects=doc['items'] if doc.get('kind')=='List' else [doc]
        for obj in objects:
            if obj.get('kind')=='Deployment' and obj['metadata']['name']=='api':
                container=obj['spec']['template']['spec']['containers'][0]
                container['env']=[e for e in container.get('env',[]) if e['name']!='LASTTABLE_TABLE_ADMIN_CODE']+[
                    {'name':'LASTTABLE_TABLE_ADMIN_CODE','valueFrom':{'secretKeyRef':{'name':'table-admin-access','key':'code'}}}]
                obj['spec']['strategy']={'type':'RollingUpdate','rollingUpdate':{'maxSurge':1,'maxUnavailable':0}}
                changed=True
    assert changed,'API deployment not found in pipeline manifest'
    encoded=base64.b64encode(yaml.safe_dump_all(documents,sort_keys=False).encode()).decode()
    api.update_deploy_artifact(identifier,oci.devops.models.UpdateDeployArtifactDetails(
        deploy_artifact_source=oci.devops.models.InlineDeployArtifactSource(base64_encoded_content=encoded)))
    cloud.save('admin-release-overlay',{'artifact_id':identifier,'secret_name':'table-admin-access','code':'stored only in Kubernetes Secret',
        'api_update':'RollingUpdate','terraform_consolidation':'deferred per user request'})
    print('Admin access Secret configured; existing DevOps deploy artifact references the Secret')


if __name__=='__main__':configure()
