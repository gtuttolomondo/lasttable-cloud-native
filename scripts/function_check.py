"""Verify Function database access and SMTP TLS/authentication without sending mail."""
import json
import oci
import cloud

out=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())['outputs']['delivery']['value']
api=oci.functions.FunctionsInvokeClient(cloud.config,service_endpoint=out['function_endpoint'],
    timeout=(10,90),retry_strategy=oci.retry.NoneRetryStrategy())
response=api.invoke_function(out['function_id'],invoke_function_body=json.dumps({'operation':'self-test'}),fn_invoke_type='sync')
answer=json.loads(response.data.content)
assert answer=={'database':'passed','smtp_tls_authentication':'passed','email_sent':False},answer
cloud.save('function-self-test',answer)
print(json.dumps(answer))
