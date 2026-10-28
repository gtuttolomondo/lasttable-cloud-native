"""Prove real OKE Workload Identity invocation and Function deduplication."""
import json
import subprocess
import cloud

booking=json.loads((cloud.PRIVATE/'cloud-acceptance-booking.json').read_text())
code='''import json,time
from lasttable.runtime import database,one,rows
from lasttable.worker import invoke_notification
with database() as db:
 row=one(db,"SELECT payload FROM LT_OWNER.outbox_events WHERE aggregate_id=:b_id AND event_type='booking.status_changed'",b_id=BOOKING_ID)
 event=row['payload'] if isinstance(row['payload'],dict) else json.loads(row['payload'])
 before=one(db,'SELECT notification_state,attempt_count FROM LT_OWNER.notification_delivery WHERE event_id=:b_id',b_id=event['event_id'])
assert before['notification_state']=='SUBMITTED' and before['attempt_count']==1
started=time.monotonic()
answer=invoke_notification(event)
elapsed=round(time.monotonic()-started,2)
assert answer=={'state':'SUBMITTED','attempts':1}
with database() as db:
 after=one(db,'SELECT notification_state,attempt_count FROM LT_OWNER.notification_delivery WHERE event_id=:b_id',b_id=event['event_id'])
assert before==after
print(json.dumps({'oke_workload_identity_invocation':'passed','function_redelivery':'deduplicated','smtp_attempts':1,'new_smtp_attempts':0,'invocation_seconds':elapsed}))
'''.replace('BOOKING_ID',repr(booking['booking_id']))
kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml'),'-n','lasttable']
result=subprocess.run(kube+['exec','-i','deployment/notifications','--','python','-'],input=code,text=True,capture_output=True)
if result.returncode:
    cloud.save('function-rehearsal-error',{'stdout':result.stdout,'stderr':result.stderr})
    raise RuntimeError('Function rehearsal failed; private diagnostic saved')
report=json.loads(result.stdout.strip().splitlines()[-1])
cloud.save('function-rehearsal-result',report)
print(json.dumps(report))
