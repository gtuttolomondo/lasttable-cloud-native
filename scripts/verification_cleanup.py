"""Review and remove only the isolated ltverify stack; never apply local state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
import oci
import cloud

assert os.environ.get('LASTTABLE_ENV')=='verify', 'This helper only supports LASTTABLE_ENV=verify'
variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
assert variables['project_name']=='ltverify'
stack=json.loads((cloud.PRIVATE/'stack.json').read_text())
assert stack['display_name']=='ltverify-demo'
rm=cloud.client(oci.resource_manager.ResourceManagerClient)

def owned_ids(state):
    return {i['attributes']['id'] for r in state['resources'] if r['mode']=='managed'
            for i in r['instances'] if str(i['attributes'].get('id','')).startswith('ocid1.')}

def review():
    state=json.loads(rm.get_stack_tf_state(stack['id']).data.content)
    primary=json.loads((cloud.ROOT/'.local/terraform-state.json').read_text())
    assert not owned_ids(state)&owned_ids(primary), 'Verification and primary ownership overlap'
    cloud.save('terraform-state',state)
    folder=cloud.PRIVATE/'destroy-review'
    folder.mkdir(exist_ok=True,mode=0o700)
    with zipfile.ZipFile(cloud.ROOT/'output/lasttable-oci-resource-manager.zip') as archive:
        archive.extractall(folder)
    providers=folder/'.terraform'
    if not providers.exists():providers.symlink_to(cloud.ROOT/'infra/.terraform',target_is_directory=True)
    # Reference the existing signing key directly. Never copy or decrypt it.
    provider={k:cloud.config[k] for k in ['tenancy','user','fingerprint','region']}
    attrs={'tenancy_ocid':provider['tenancy'],'user_ocid':provider['user'],
           'fingerprint':provider['fingerprint'],'private_key_path':cloud.config['key_file'],
           'region':provider['region']}
    body='\n'.join(k+' = '+json.dumps(v) for k,v in attrs.items())
    (folder/'cleanup_auth.tf').write_text('variable "cleanup_signing_passphrase" {\n type = string\n sensitive = true\n default = null\n}\n')
    override=''
    for alias in ['', 'identity_home']:
        override+='provider "oci" {\n'+('alias = "identity_home"\n' if alias else '')+body+'\nprivate_key_password = var.cleanup_signing_passphrase\n}\n'
    (folder/'cleanup_override.tf').write_text(override)
    varfile=folder/'private.tfvars.json';varfile.write_text(json.dumps(variables));varfile.chmod(0o600)
    env={**os.environ,'TF_VAR_cleanup_signing_passphrase':cloud.config.get('pass_phrase') or ''}
    binary=cloud.ROOT/'.tools/terraform';plan=folder/'destroy.tfplan'
    command=[str(binary),'-chdir='+str(folder),'plan','-destroy','-refresh=false','-input=false','-no-color',
        '-state='+str(cloud.PRIVATE/'terraform-state.json'),'-var-file='+str(varfile),'-out='+str(plan)]
    result=subprocess.run(command,env=env,capture_output=True,text=True)
    (folder/'plan.log').write_text(result.stdout+'\n'+result.stderr)
    assert result.returncode==0,'Read-only destroy plan failed; private diagnostic saved'
    raw=subprocess.check_output([str(binary),'-chdir='+str(folder),'show','-json',str(plan)],env=env,text=True)
    parsed=json.loads(raw);cloud.save('destroy-plan',parsed)
    changes=[r for r in parsed['resource_changes'] if r['mode']=='managed']
    assert changes and all(r['change']['actions']==['delete'] for r in changes)
    report={'stack_id':stack['id'],'state_sha256':hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest(),
        'destroy_count':len(changes),'addresses':[r['address'] for r in changes],
        'primary_resource_overlap':False,'local_apply':False}
    cloud.save('destroy-review',report)
    print(json.dumps({'destroy_count':len(changes),'primary_resource_overlap':False,'review':'saved; no resources removed'}))

def destroy():
    report=json.loads((cloud.PRIVATE/'destroy-review.json').read_text())
    assert report['stack_id']==stack['id'] and not report['primary_resource_overlap']
    current=json.loads(rm.get_stack_tf_state(stack['id']).data.content)
    assert hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()==report['state_sha256'],'Stack changed after review'
    jobs=rm.list_jobs(stack_id=stack['id']).data
    assert not any(j.lifecycle_state in ['ACCEPTED','IN_PROGRESS'] for j in jobs),'Stack job still running'
    kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]
    resources=json.loads(subprocess.check_output(kube+['get','services,pvc','-n','lasttable','-o','json'],text=True))
    assert not any(r['kind']=='PersistentVolumeClaim' or r.get('spec',{}).get('type')=='LoadBalancer' for r in resources['items']), 'Remove verification TLS PVC and LoadBalancer Service first'
    delivery=current['outputs']['delivery']['value']
    network=cloud.client(oci.core.VirtualNetworkClient)
    assert not network.get_public_ip(delivery['public_ip_id']).data.private_ip_id,'Verification address is still attached'
    devops=cloud.client(oci.devops.DevopsClient)
    outputs=current['outputs'].get('devops',{}).get('value')
    if outputs:
        runs=devops.list_build_runs(build_pipeline_id=outputs['build_pipeline_id']).data.items
        deployments=devops.list_deployments(deploy_pipeline_id=outputs['deploy_pipeline_id']).data.items
        assert not any(r.lifecycle_state in ['ACCEPTED','IN_PROGRESS'] for r in [*runs,*deployments])
    bastion=current['outputs']['security']['value']['bastion_id']
    bastion_api=cloud.client(oci.bastion.BastionClient)
    for session in bastion_api.list_sessions(bastion).data:
        if session.lifecycle_state not in ['DELETED','DELETING']:
            bastion_api.delete_session(session.id)
    result=rm.create_job(oci.resource_manager.models.CreateJobDetails(stack_id=stack['id'],display_name='Remove isolated ltverify only',
        job_operation_details=oci.resource_manager.models.CreateDestroyJobOperationDetails(operation='DESTROY',execution_plan_strategy='AUTO_APPROVED'))).data
    cloud.save('latest-destroy',result)
    print(json.dumps({'id':result.id,'state':result.lifecycle_state,'stack':'ltverify-demo only'}))

def remove_workloads():
    state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
    api=cloud.client(oci.devops.DevopsClient)
    pipeline=state['outputs']['devops']['value']['deploy_pipeline_id']
    deployments=api.list_deployments(deploy_pipeline_id=pipeline).data.items
    assert deployments and all(d.lifecycle_state not in ['ACCEPTED','IN_PROGRESS'] for d in deployments)
    assert (cloud.PRIVATE/'clean-acceptance-result.json').exists(),'Record successful clean rehearsal first'
    kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml')]
    resources=json.loads(subprocess.check_output(kube+['get','services,pvc','-n','lasttable','-o','json'],text=True))
    handles=[]
    for item in resources['items']:
        if item['kind']=='PersistentVolumeClaim' and item.get('spec',{}).get('volumeName'):
            pv=json.loads(subprocess.check_output(kube+['get','pv',item['spec']['volumeName'],'-o','json'],text=True))
            handles.append(pv['spec']['csi']['volumeHandle'])
    cloud.save('kubernetes-cleanup',{'namespace':'lasttable','cluster':state['outputs']['platform']['value']['cluster_id'],
        'volume_ids':handles,'reserved_ip':state['outputs']['delivery']['value']['public_ip_id']})
    subprocess.run(kube+['delete','namespace','lasttable','--wait=false'],check=True)
    print('Only the verified ltverify namespace was removed; wait for load balancer and volume deletion before Destroy')

def status():
    saved=json.loads((cloud.PRIVATE/'latest-destroy.json').read_text())
    job=rm.get_job(saved['id']).data;cloud.save('latest-destroy',job)
    logs=oci.pagination.list_call_get_all_results(rm.get_job_logs,job.id).data;cloud.save('logs-destroy',logs)
    print(json.dumps({'state':job.lifecycle_state,'failure':oci.util.to_dict(job.failure_details)}))
    for entry in logs[-12:]:print(entry.message)
    if job.lifecycle_state in ['SUCCEEDED','FAILED']:
        cloud.save('terraform-state',json.loads(rm.get_job_tf_state(job.id).data.content))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['review','remove-workloads','destroy','status'])
    {'review':review,'remove-workloads':remove_workloads,'destroy':destroy,'status':status}[parser.parse_args().action]()
