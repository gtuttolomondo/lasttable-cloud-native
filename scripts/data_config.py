"""Resolve private service metadata for the bootstrap workload; never read passwords."""
import json
import oci
import cloud

variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
prefix=variables.get('project_name','lasttable')
state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
vault=state['outputs']['security']['value']['vault_id']
databases=cloud.client(oci.database.DatabaseClient).list_autonomous_databases(cloud.COMPARTMENT).data
database=next(d for d in databases if d.display_name==prefix+'-adb' and d.lifecycle_state=='AVAILABLE')
database=cloud.client(oci.database.DatabaseClient).get_autonomous_database(database.id).data
profiles=[p for p in database.connection_strings.profiles if p.tls_authentication=='SERVER'
    and p.consumer_group=='LOW' and p.syntax_format=='LONG']
assert profiles,'Expected a walletless TLS server-authentication LOW connection profile'
secrets=oci.pagination.list_call_get_all_results(cloud.client(oci.vault.VaultsClient).list_secrets,
    cloud.COMPARTMENT,vault_id=vault).data
identifiers={s.secret_name.removeprefix(prefix+'-'):s.id for s in secrets if s.secret_name.startswith(prefix+'-') and s.lifecycle_state=='ACTIVE'}
config={'region':cloud.config['region'],'database_id':database.id,'database_dsn':profiles[0].value,
    'database_private_ip':database.private_endpoint_ip,'credential_secret_ids':{k:v for k,v in identifiers.items() if k!='kafka-admin'},
    'kafka_admin_secret_id':identifiers.get('kafka-admin'),'kafka_admin_username':'superuser'}
kafka=cloud.client(oci.managed_kafka.KafkaClusterClient).list_kafka_clusters(compartment_id=cloud.COMPARTMENT).data.items
matches=[k for k in kafka if k.display_name==prefix+'-kafka' and k.lifecycle_state=='ACTIVE']
if matches:
    cluster=cloud.client(oci.managed_kafka.KafkaClusterClient).get_kafka_cluster(matches[0].id).data
    config['kafka_id']=cluster.id
    urls=[u.url for u in cluster.kafka_bootstrap_urls if 'SASL' in u.name.upper()]
    if not urls: urls=[u.url for u in cluster.kafka_bootstrap_urls]
    config['kafka_bootstrap_servers']=','.join(urls)
cloud.save('bootstrap-config',config)
print(json.dumps({'database':'AVAILABLE','credential_references':len(identifiers),'kafka_endpoint_ready':bool(config.get('kafka_bootstrap_servers'))}))
