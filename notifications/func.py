"""OCI Functions FDK adapter; synchronous consumer invocation is the only trigger."""
import io
import json
import smtplib
import ssl
from fdk import response
from lasttable.notification import notify
from lasttable.runtime import config, database, one, secret


def handler(ctx, data: io.BytesIO = None):
    try:
        payload = json.loads(data.getvalue())
        if payload == {'operation': 'self-test'}:
            with database('notify') as db:
                assert one(db, 'SELECT 1 AS healthy FROM dual')['healthy']==1
            cfg=config()
            with smtplib.SMTP(cfg['smtp_host'], int(cfg.get('smtp_port',587)), timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(secret('smtp-username'),secret('smtp-password'))
            answer={'database':'passed','smtp_tls_authentication':'passed','email_sent':False}
        else:
            answer = notify(payload)
            print(json.dumps({'component':'function','event_id':payload['event_id'],
                'correlation_id':payload.get('correlation_id'),'state':answer['state']}),flush=True)
        return response.Response(ctx, response_data=json.dumps(answer), headers={'Content-Type': 'application/json'})
    except (ValueError, KeyError, TypeError):
        return response.Response(ctx, status_code=400, response_data='{"error":"INVALID_EVENT"}', headers={'Content-Type': 'application/json'})
