"""Resolve non-secret runtime references for live checks and private operational tools."""
import json
import cloud

state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())['outputs']
bootstrap=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
delivery=state['delivery']['value']
ids={key:value for key,value in bootstrap['credential_secret_ids'].items()
     if key in ['database-api','database-worker','kafka-producer','kafka-confirmation','kafka-trending','kafka-notifications','kafka-observer']}
ids['demo-admin']=delivery['demo_admin_secret_id']
cfg={'region':cloud.config['region'],'database_dsn':bootstrap['database_dsn'],
     'kafka_bootstrap_servers':bootstrap['kafka_bootstrap_servers'],'credential_secret_ids':ids,
     'function_id':delivery['function_id'],'function_endpoint':delivery['function_endpoint']}
cloud.save('application-config',cfg)
print('Non-secret application runtime references saved privately')
