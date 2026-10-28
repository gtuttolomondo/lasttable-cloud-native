"""Re-deliver the authorized booking and exercise malformed-event DLQs on OKE."""
import json
import subprocess
import cloud
from cloud_acceptance import call, until, dashboard

kube=[str(cloud.ROOT/'.tools/kubectl'),'--kubeconfig',str(cloud.PRIVATE/'kubeconfig.yaml'),'-n','lasttable']
booking=json.loads((cloud.PRIVATE/'cloud-acceptance-booking.json').read_text())
status='/api/bookings/'+booking['booking_id']
assert call('GET',status)['notification']['notification_state']=='SUBMITTED'

def inside(role,code):
    result=subprocess.run(kube+['exec','-i','deployment/'+role,'--','python','-'],input=code,capture_output=True,text=True)
    if result.returncode:
        cloud.save('kafka-rehearsal-error',{'stdout':result.stdout,'stderr':result.stderr})
        raise RuntimeError('Private Kafka rehearsal diagnostic saved')
    return json.loads(result.stdout.strip().splitlines()[-1])

offset_code='''import json
from confluent_kafka import Consumer,TopicPartition
from lasttable.runtime import kafka_settings
c=Consumer({**kafka_settings('observer'),'group.id':'lasttable-confirmation','enable.auto.commit':False})
result={}
try:
 for topic in ['lasttable.bookings.created','lasttable.bookings.status-changed','lasttable.bookings.dlq','lasttable.notifications.dlq']:
  meta=c.list_topics(topic,timeout=10)
  result[topic]=sum(c.get_watermark_offsets(TopicPartition(topic,p),timeout=10)[1] for p in meta.topics[topic].partitions)
finally:c.close()
print(json.dumps(result))
'''
record=cloud.PRIVATE/'kafka-rehearsal-produced.json'
if not record.exists():
    before=inside('observer',offset_code)
    code='''import json
from confluent_kafka import Producer
from lasttable.runtime import database,rows,kafka_settings
from lasttable.worker import produce
with database() as db:
 events=rows(db,'SELECT topic,payload FROM LT_OWNER.outbox_events WHERE aggregate_id=:b_id',b_id=BOOKING_ID)
assert len(events)==2
p=Producer({**kafka_settings('producer'),'enable.idempotence':True})
for row in events:
 event=row['payload'] if isinstance(row['payload'],dict) else json.loads(row['payload'])
 produce(p,row['topic'],BOOKING_ID,event)
 produce(p,row['topic'],'malformed-rehearsal',{'schema_version':999})
print(json.dumps({'duplicate_events':2,'malformed_events':2}))
'''.replace('BOOKING_ID',repr(booking['booking_id']))
    produced=inside('publisher',code)
    cloud.save('kafka-rehearsal-produced',{'before':before,**produced})
before=json.loads(record.read_text())['before']
until(lambda:all(((dashboard()['kafka'] or {}).get(role,{}).get('end') or 0)>=before[topic]+2
    and (dashboard()['kafka'] or {}).get(role,{}).get('lag')==0
    for role,topic in [('confirmation','lasttable.bookings.created'),('trending','lasttable.bookings.created'),('notifications','lasttable.bookings.status-changed')]))
after=inside('observer',offset_code)
assert after['lasttable.bookings.dlq']>=before['lasttable.bookings.dlq']+2
assert after['lasttable.notifications.dlq']>=before['lasttable.notifications.dlq']+1
assert call('GET',status)['notification']['attempt_count']==1
request=call('POST','/api/admin/replay',admin=True)
replay=until(lambda:next((r for r in dashboard()['replays'] if r['run_id']==request['run_id'] and r['run_state'] in ['COMPLETE','FAILED']),None))
assert replay['run_state']=='COMPLETE'
expected=json.loads(replay['expected_json']) if isinstance(replay['expected_json'],str) else replay['expected_json']
actual=json.loads(replay['actual_json']) if isinstance(replay['actual_json'],str) else replay['actual_json']
assert replay['records_processed']==sum(expected.values()) and expected==actual
assert call('GET',status)['notification']['attempt_count']==1
report={'real_kafka_redelivery':'two valid events, no additional SMTP attempt',
    'malformed_records':'both consumers continue and advance offsets',
    'booking_dlq_delta':after['lasttable.bookings.dlq']-before['lasttable.bookings.dlq'],
    'notification_dlq_delta':after['lasttable.notifications.dlq']-before['lasttable.notifications.dlq'],
    'replay':f'equal, {replay["records_processed"]} unique bookings; malformed and duplicate records skipped','smtp_attempts':1}
cloud.save('kafka-rehearsal-result',report)
print(json.dumps(report))
