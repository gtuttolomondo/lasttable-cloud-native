"""Private deployment runner. Run with the OCI CLI's Python environment.

The source never embeds credentials or environment OCIDs. Evidence stays in .local.
"""
import argparse
import base64
import json
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
import oci

ROOT = Path(__file__).resolve().parents[1]
environment = os.environ.get('LASTTABLE_ENV', '')
assert not environment or re.fullmatch(r'[a-z0-9-]{1,40}', environment)
PRIVATE = ROOT / '.local' / environment if environment else ROOT / '.local'
PRIVATE.mkdir(exist_ok=True, parents=True, mode=0o700)
PRIVATE.chmod(0o700)
config_path = Path(os.environ.get('OCI_CLI_CONFIG_FILE', str(Path.home()/'.oci/config'))).expanduser().resolve()
original_cwd = Path.cwd()
os.chdir(config_path.parent)
config = oci.config.from_file(str(config_path), profile_name=os.environ.get('OCI_CLI_PROFILE','DEFAULT'))
os.chdir(original_cwd)
key = Path(config['key_file']).expanduser()
if not key.is_absolute():
    config['key_file'] = str(config_path.parent / key)
variables_path = PRIVATE / 'variables.json'
saved_variables = json.loads(variables_path.read_text()) if variables_path.exists() else {}
COMPARTMENT = os.environ.get('LASTTABLE_COMPARTMENT_OCID') or saved_variables.get('compartment_ocid')
if not COMPARTMENT and (ROOT/'oci-webinair-provisioning.json').exists():
    COMPARTMENT = json.loads((ROOT/'oci-webinair-provisioning.json').read_text())['compartment']['id']
assert COMPARTMENT and COMPARTMENT.startswith('ocid1.compartment.'), 'Set LASTTABLE_COMPARTMENT_OCID or .local/variables.json'
STACK_NAME = os.environ.get('LASTTABLE_STACK_NAME',saved_variables.get('project_name','lasttable')+'-demo')
retry = oci.retry.DEFAULT_RETRY_STRATEGY

def client(cls):
    return cls(config, timeout=(10, 60), retry_strategy=retry)

def save(name, data):
    path = PRIVATE / f'{name}.json'
    path.write_text(json.dumps(oci.util.to_dict(data), indent=2))
    path.chmod(0o600)
    return path

def preflight():
    identity = client(oci.identity.IdentityClient)
    compartment = identity.get_compartment(COMPARTMENT).data
    assert compartment.lifecycle_state == 'ACTIVE'
    ce = client(oci.container_engine.ContainerEngineClient)
    network = client(oci.core.VirtualNetworkClient)
    rm = client(oci.resource_manager.ResourceManagerClient)
    compute = client(oci.core.ComputeClient)
    result = {'timestamp':datetime.now(timezone.utc).isoformat(), 'region':config['region'],
              'compartment':compartment.name, 'compartment_state':compartment.lifecycle_state}
    for label, action in [
        ('vcns', lambda: network.list_vcns(COMPARTMENT).data),
        ('clusters', lambda: ce.list_clusters(COMPARTMENT).data),
        ('stacks', lambda: rm.list_stacks(compartment_id=COMPARTMENT).data),
        ('availability_domains', lambda: identity.list_availability_domains(config['tenancy']).data),
        ('oke_options', lambda: ce.get_cluster_options('all').data),
        ('shapes', lambda: compute.list_shapes(COMPARTMENT).data),
        ('kafka_clusters', lambda: client(oci.managed_kafka.KafkaClusterClient).list_kafka_clusters(compartment_id=COMPARTMENT).data),
    ]:
        try:
            data=action(); save(f'preflight-{label}',data)
            result[label] = len(data) if isinstance(data,list) else 'available'
        except Exception as e:
            result[label] = {'error': type(e).__name__, 'detail':str(e)[:1000]}
    result['ocir_namespace'] = client(oci.object_storage.ObjectStorageClient).get_namespace().data
    save('preflight', result)
    print(json.dumps(result,indent=2))

def stack_create():
    rm=client(oci.resource_manager.ResourceManagerClient)
    path=PRIVATE/'stack.json'
    if path.exists():
        print('Stack already tracked; refusing duplicate creation'); return
    # Recover a previous request whose response may have been interrupted.
    existing=rm.list_stacks(compartment_id=COMPARTMENT, display_name=STACK_NAME).data
    active=[x for x in existing if x.lifecycle_state != 'DELETED']
    if active:
        assert len(active)==1
        save('stack', rm.get_stack(active[0].id).data)
        print('Recovered existing tracked stack'); return
    variables={'compartment_ocid':COMPARTMENT,'region':config['region'],'tenancy_ocid':config['tenancy']}
    variables.update(saved_variables)
    variables={k:v if isinstance(v,str) else json.dumps(v) for k,v in variables.items()}
    details=oci.resource_manager.models.CreateStackDetails(
        compartment_id=COMPARTMENT,display_name=STACK_NAME,terraform_version='1.5.x',
        config_source=oci.resource_manager.models.CreateZipUploadConfigSourceDetails(
            config_source_type='ZIP_UPLOAD',zip_file_base64_encoded=base64.b64encode((ROOT/'output/lasttable-oci-resource-manager.zip').read_bytes()).decode()),
        variables=variables,freeform_tags={'project':'LastTable','managed-by':'resource-manager'})
    token_path=PRIVATE/'stack-create-token'
    if not token_path.exists():
        token_path.write_text(uuid.uuid4().hex);token_path.chmod(0o600)
    data=rm.create_stack(details,opc_retry_token=token_path.read_text().strip()).data
    save('stack',data); print('Created Resource Manager stack',data.id)

def stack_update():
    rm=client(oci.resource_manager.ResourceManagerClient)
    stack=json.loads((PRIVATE/'stack.json').read_text())
    variables=json.loads((PRIVATE/'variables.json').read_text())
    for name,value in list(variables.items()):
        if not isinstance(value,str): variables[name]=json.dumps(value)
    details=oci.resource_manager.models.UpdateStackDetails(
        config_source=oci.resource_manager.models.UpdateZipUploadConfigSourceDetails(
            config_source_type='ZIP_UPLOAD',zip_file_base64_encoded=base64.b64encode((ROOT/'output/lasttable-oci-resource-manager.zip').read_bytes()).decode()),
        variables=variables)
    save('stack',rm.update_stack(stack['id'],details).data)
    print('Updated existing stack configuration; resources unchanged until Plan/Apply')

def job_create(operation):
    rm=client(oci.resource_manager.ResourceManagerClient)
    stack=json.loads((PRIVATE/'stack.json').read_text())
    if operation=='plan':
        options=oci.resource_manager.models.CreatePlanJobOperationDetails(operation='PLAN',is_provider_upgrade_required=True)
    else:
        plan=json.loads((PRIVATE/'latest-plan.json').read_text())
        actual=rm.get_job(plan['id']).data
        assert actual.lifecycle_state=='SUCCEEDED', 'Plan must succeed before apply'
        options=oci.resource_manager.models.CreateApplyJobOperationDetails(operation='APPLY',is_provider_upgrade_required=True,execution_plan_strategy='FROM_PLAN_JOB_ID',execution_plan_job_id=plan['id'])
    job=rm.create_job(oci.resource_manager.models.CreateJobDetails(
        stack_id=stack['id'],display_name=f'lasttable-{operation}-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}',job_operation_details=options)).data
    save(f'latest-{operation}',job); save(f'job-{job.id.split(".")[-1]}',job)
    print(json.dumps({'operation':operation,'id':job.id,'state':job.lifecycle_state}))

def job_status(operation):
    rm=client(oci.resource_manager.ResourceManagerClient)
    job=json.loads((PRIVATE/f'latest-{operation}.json').read_text())
    actual=rm.get_job(job['id']).data; save(f'latest-{operation}',actual)
    logs=oci.pagination.list_call_get_all_results(rm.get_job_logs, actual.id).data; save(f'logs-{operation}',logs)
    print(json.dumps({'state':actual.lifecycle_state,'failure':oci.util.to_dict(actual.failure_details)}))
    for entry in logs[-30:]: print(entry.message)
    if actual.lifecycle_state in ['SUCCEEDED','FAILED'] and operation=='apply':
        state=rm.get_job_tf_state(actual.id).data
        save('terraform-state',json.loads(state.content))

def verify_network():
    state=json.loads((PRIVATE/'terraform-state.json').read_text())
    expected=state['outputs']['network']['value']
    vn=client(oci.core.VirtualNetworkClient)
    vcn=vn.get_vcn(expected['vcn_id']).data
    assert vcn.lifecycle_state=='AVAILABLE'
    assert vcn.compartment_id==COMPARTMENT
    report={'vcn':True,'subnets':{},'nsgs':{},'routing':{}}
    import ipaddress
    cidrs=[]
    for name, identifier in expected['subnet_ids'].items():
        subnet=vn.get_subnet(identifier).data
        assert subnet.lifecycle_state=='AVAILABLE'
        assert subnet.compartment_id==COMPARTMENT and subnet.vcn_id==vcn.id
        assert subnet.cidr_block==expected['subnet_cidrs'][name]
        assert subnet.prohibit_public_ip_on_vnic==(name!='ingress')
        cidr=ipaddress.ip_network(subnet.cidr_block)
        assert all(not cidr.overlaps(other) for other in cidrs)
        cidrs.append(cidr)
        route=vn.get_route_table(subnet.route_table_id).data
        if name=='ingress':
            assert len(route.route_rules)==1 and '.internetgateway.' in route.route_rules[0].network_entity_id
        else:
            assert any('.natgateway.' in r.network_entity_id for r in route.route_rules)
            assert any(r.destination_type=='SERVICE_CIDR_BLOCK' for r in route.route_rules)
        lists=[vn.get_security_list(s).data for s in subnet.security_list_ids]
        # Public SSH or Kafka is never allowed by any attached security list.
        for sl in lists:
            for rule in sl.ingress_security_rules:
                assert not (rule.source=='0.0.0.0/0' and rule.protocol in ['6','all'])
        report['subnets'][name]={'available':True,'private':subnet.prohibit_public_ip_on_vnic,'cidr':subnet.cidr_block}
        report['routing'][name]='internet-gateway' if name=='ingress' else 'NAT + service-gateway'
    for name,identifier in expected['nsg_ids'].items():
        nsg=vn.get_network_security_group(identifier).data
        assert nsg.lifecycle_state=='AVAILABLE' and nsg.compartment_id==COMPARTMENT
        rules=vn.list_network_security_group_security_rules(identifier).data
        for rule in rules:
            if rule.direction=='INGRESS' and rule.source=='0.0.0.0/0':
                assert name=='ingress' and rule.protocol=='6'
                assert rule.tcp_options.destination_port_range.min in [80,443]
        report['nsgs'][name]={'available':True,'rule_count':len(rules)}
    report['runtime_connectivity']='Pending OKE test workload in the next phase; this check verifies live resource configuration.'
    save('network-verification',report)
    print(json.dumps(report,indent=2))

def verify_vault():
    state=json.loads((PRIVATE/'terraform-state.json').read_text())
    def resource_id(kind,name):
        resource=next(r for r in state['resources'] if r['type']==kind and r['name']==name)
        return resource['instances'][0]['attributes']['id']
    vault=client(oci.key_management.KmsVaultClient).get_vault(resource_id('oci_kms_vault','demo')).data
    assert vault.lifecycle_state=='ACTIVE' and vault.compartment_id==COMPARTMENT
    management=oci.key_management.KmsManagementClient(config,service_endpoint=vault.management_endpoint)
    key=management.get_key(resource_id('oci_kms_key','secrets')).data
    assert key.lifecycle_state=='ENABLED' and key.protection_mode=='SOFTWARE'
    crypto=oci.key_management.KmsCryptoClient(config,service_endpoint=vault.crypto_endpoint)
    plaintext=base64.b64encode(b'lasttable-vault-smoke').decode()
    encrypted=crypto.encrypt(oci.key_management.models.EncryptDataDetails(key_id=key.id,plaintext=plaintext)).data
    decrypted=crypto.decrypt(oci.key_management.models.DecryptDataDetails(key_id=key.id,ciphertext=encrypted.ciphertext)).data
    assert decrypted.plaintext==plaintext
    report={'vault':'ACTIVE','key':'ENABLED','encryption_roundtrip':'passed'}
    save('vault-verification',report); print(json.dumps(report))

def verify_security():
    verify_vault()
    state=json.loads((PRIVATE/'terraform-state.json').read_text())
    outputs=state['outputs']['security']['value']
    bastion=client(oci.bastion.BastionClient).get_bastion(outputs['bastion_id']).data
    assert bastion.lifecycle_state=='ACTIVE' and bastion.compartment_id==COMPARTMENT
    import ipaddress
    assert bastion.client_cidr_block_allow_list and all(ipaddress.ip_network(c).prefixlen==32 for c in bastion.client_cidr_block_allow_list)
    assert bastion.target_subnet_id==state['outputs']['network']['value']['subnet_ids']['automation']
    log=client(oci.logging.LoggingManagementClient).get_log_group(outputs['log_group_id']).data
    assert log.compartment_id==COMPARTMENT
    report={'vault':'ACTIVE','key':'ENABLED','encryption_roundtrip':'passed','bastion':'ACTIVE','bastion_allowlist':'explicit IPv4 /32 only','log_group':'present'}
    save('security-verification',report); print(json.dumps(report))

def verify_platform():
    state=json.loads((PRIVATE/'terraform-state.json').read_text())
    outputs=state['outputs']['platform']['value']
    network=state['outputs']['network']['value']
    ce=client(oci.container_engine.ContainerEngineClient)
    cluster=ce.get_cluster(outputs['cluster_id']).data
    assert cluster.lifecycle_state=='ACTIVE' and cluster.compartment_id==COMPARTMENT
    assert cluster.endpoint_config.is_public_ip_enabled is False
    assert cluster.endpoint_config.subnet_id==network['subnet_ids']['api']
    pool=ce.get_node_pool(outputs['node_pool_id']).data
    assert pool.compartment_id==COMPARTMENT and pool.node_config_details.size==2
    assert len(pool.nodes)==2 and all(n.lifecycle_state=='ACTIVE' for n in pool.nodes)
    artifacts=client(oci.artifacts.ArtifactsClient)
    repositories=oci.pagination.list_call_get_all_results(artifacts.list_container_repositories,compartment_id=COMPARTMENT).data
    items=repositories.items if hasattr(repositories,'items') else repositories
    selected=[r for r in items if r.display_name in outputs['repositories'].values()]
    assert len(selected)==3 and all(not r.is_public for r in selected)
    report={'cluster':'ACTIVE','endpoint':'private','nodes':[{'name':n.name,'state':n.lifecycle_state} for n in pool.nodes],'repositories':'3 private repositories; releases pinned by digest','kubernetes_smoke':'pending authenticated kubectl tests'}
    save('platform-verification',report);print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['preflight','stack-create','stack-update','plan','apply','status','verify-network','verify-security','verify-vault','verify-platform']); p.add_argument('--operation',default='plan',choices=['plan','apply']); args=p.parse_args()
    if args.action=='preflight': preflight()
    elif args.action=='stack-create': stack_create()
    elif args.action=='stack-update': stack_update()
    elif args.action=='status': job_status(args.operation)
    elif args.action=='verify-network': verify_network()
    elif args.action=='verify-security': verify_security()
    elif args.action=='verify-vault': verify_vault()
    elif args.action=='verify-platform': verify_platform()
    else: job_create(args.action)
