"""Create ignored local runtime mounts by reading Vault with the developer identity."""
import base64
import json
import secrets
import cloud
import oci

cfg=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
cfg['database_dsn']=cfg['database_dsn'].replace('(port=1521)','(port=11521)')
cfg['local_kafka']=True
cfg['kafka_bootstrap_servers']='lasttable-kafka:9092'
cfg['local_function_url']='http://lasttable-notifications:8080/invoke'
cfg['smtp_host']='lasttable-mailpit';cfg['smtp_port']=1025;cfg['smtp_local_capture']=True
variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
cfg['sender']='noreply@'+variables['demo_domain']
client=cloud.client(oci.secrets.SecretsClient)
values={}
for name in ['database-api','database-worker','database-notify']:
    bundle=client.get_secret_bundle(cfg['credential_secret_ids'][name],stage='CURRENT').data
    values[name]=base64.b64decode(bundle.secret_bundle_content.content).decode()
previous=cloud.PRIVATE/'local-secrets.json'
old=json.loads(previous.read_text()) if previous.exists() else {}
values['demo-admin']=old.get('demo-admin',secrets.token_urlsafe(32))
values['table-admin']=old.get('table-admin',secrets.token_urlsafe(32))
cloud.save('local-secrets',values)
cloud.save('local-config',cfg)
print('Local configuration and restricted runtime secrets saved under ignored .local/')
