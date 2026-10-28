"""Recreated environment rehearsal with notification delivery kept paused."""
import json
import os
import uuid
import cloud
from cloud_acceptance import call, until, dashboard

assert os.environ.get('LASTTABLE_ENV')=='verify'
variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
assert variables['project_name']=='ltverify'
health=call('GET','/api/health');call('GET','/api/ready')
call('POST','/api/admin/notifications',{'paused':True},admin=True)
until(lambda:next(iter(sorted((w for w in dashboard()['workers'] if w['component']=='notifications'),key=lambda w:w['last_seen_at'],reverse=True)),{}).get('worker_state')=='PAUSED')
path=cloud.PRIVATE/'clean-acceptance-booking.json'
if not path.exists():
    recipient=json.loads((cloud.ROOT/'.local/test-recipient.json').read_text())['email']
    cloud.save('clean-acceptance-booking',{'booking_id':str(uuid.uuid4()),'restaurant_id':'aurora',
        'nickname':'Verifica ricreazione','email':recipient,'party_size':2,'reservation_time':'20:00'})
booking=json.loads(path.read_text())
call('POST','/api/bookings',booking)
call('POST','/api/admin/bookings/decision',{'state':'CONFIRMED','booking_ids':[booking['booking_id']]},admin=True)
status='/api/bookings/'+booking['booking_id']
until(lambda:call('GET',status)['booking_state']=='CONFIRMED')
until(lambda:((dashboard()['kafka'] or {}).get('notifications',{}).get('lag') or 0)>=1)
request=call('POST','/api/admin/replay',admin=True)
replay=until(lambda:next((r for r in dashboard()['replays'] if r['run_id']==request['run_id'] and r['run_state'] in ['COMPLETE','FAILED']),None))
assert replay['run_state']=='COMPLETE' and replay['records_processed']==1
assert replay['expected_json']==replay['actual_json']
assert call('GET',status)['notification']['attempt_count']==0
assert not any(n['notification_state']=='SUBMITTED' for n in dashboard()['notifications'])
report={'https':'verified','release':health['release'],'database':'ready','booking':'CONFIRMED',
        'kafka_lag':'visible with notification consumer paused','replay':'one unique event, equal projection',
        'smtp_attempts':0,'notification_consumer':'left paused until verification environment cleanup'}
cloud.save('clean-acceptance-result',report)
print(json.dumps(report))
