"""Read-only pre-release probes using existing OKE identities, with no test bookings."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
KUBE=[str(ROOT/'.tools/kubectl'),'--kubeconfig',str(ROOT/'.local/kubeconfig.yaml'),'-n','lasttable']
source=(ROOT/'app/lasttable/observability.py').read_text()
prelude="import sys,types,json\nm=types.ModuleType('lasttable.observability');sys.modules[m.__name__]=m\nexec("+repr(source)+",m.__dict__)\n"
api_code=prelude+'''
from lasttable.runtime import database,current_session
with database('api') as db:
 sid=current_session(db)['session_id']
 messages=m.kafka_messages(db,sid)
 assert len(messages['messages'])<=100
 for message in messages['messages']:
  assert set(message['content'])=={'schema_version','event_id','event_type','public_alias','restaurant_id','correlation_id','state','state_version','occurred_at'}
  assert message['partition_id'] is not None and message['offset'] is not None
 print(json.dumps({'message_query':'passed','public_messages':len(messages['messages'])}))
 report=m.dashboard_metrics(db,sid)
 assert len(report['topics'])==4
 assert len(report['throughput'])==15
 for booking in report['recent_bookings'][:3]:
  trace=m.booking_trace(db,sid,booking['public_alias'])
  serialized=json.dumps(trace,default=str)
  assert 'recipient_email' not in serialized and 'nickname' not in serialized
 print(json.dumps({'sql':'passed','topics':len(report['topics']),'chart_buckets':len(report['throughput']),
  'bookings':len(report['recent_bookings']),'trace_privacy':'passed'}))
'''
worker=(ROOT/'app/lasttable/worker.py').read_text()
worker_code=prelude+"\nw=types.ModuleType('lasttable.observer_probe')\nexec("+repr(worker)+",w.__dict__)\n"+'''
from contextlib import contextmanager
@contextmanager
def fake_database(*args):yield None
captured=[]
w.database=fake_database;w.heartbeat=lambda *args:None;w.current_session=lambda db:{'session_id':'read-only-probe'}
w.log=lambda db,event,component,action,**kw:captured.append((action,kw))
class Session:
 def commit(self):pass
@contextmanager
def fake_database(*args):yield Session()
w.database=fake_database
original_sleep=w.time.sleep
def stop(*args):w.STOP=True
w.time.sleep=stop
try:w.observer()
finally:w.time.sleep=original_sleep
assert all(len(kw.get('detail',''))<=400 for action,kw in captured)
for action,kw in captured:
 value=json.loads(kw['detail'])
 if action=='kafka.inventory.snapshot':
  assert value['observed_topics']==4,value
  print(json.dumps({'kafka_inventory':value}))
 if action=='kafka.snapshot':
  assert all(v['lag'] is not None for v in value.values()),value
  print(json.dumps({'kafka_groups':value}))
print('PASS: real Kafka read-only probe; all snapshot payloads fit existing database column')
'''
for deployment,code in [('api',api_code),('observer',worker_code)]:
 result=subprocess.run(KUBE+['exec','-i','deployment/'+deployment,'--','python','-'],input=code,text=True,capture_output=True)
 print(result.stdout)
 if result.returncode:
  print(result.stderr)
  raise SystemExit(result.returncode)
