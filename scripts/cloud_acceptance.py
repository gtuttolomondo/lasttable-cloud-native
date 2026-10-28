"""HTTPS rehearsal; sends to the explicitly configured private test recipient.

The booking UUID is persisted before submission so reruns cannot duplicate mail.
Mailbox delivery must be verified separately from this SMTP submission check.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import time
import uuid
import oci
from oci._vendor import requests
import cloud

variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
base='https://'+variables.get('project_name','lasttable')+'.'+variables['demo_domain']
config=json.loads((cloud.PRIVATE/'application-config.json').read_text())
bundle=cloud.client(oci.secrets.SecretsClient).get_secret_bundle(config['credential_secret_ids']['demo-admin']).data
token=base64.b64decode(bundle.secret_bundle_content.content).decode()
headers={'Authorization':'Bearer '+token}


def call(method,path,payload=None,admin=False):
    response=requests.request(method,base+path,json=payload,headers=({'Authorization':'Bearer '+json.loads((cloud.PRIVATE/'table-admin-code.json').read_text())['code']} if path=='/api/admin/bookings/decision' else headers) if admin else {},timeout=30)
    response.raise_for_status()
    return response.json()


def until(check,seconds=150):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        result=check()
        if result:return result
        time.sleep(2)
    raise AssertionError('Cloud acceptance condition timed out')


def dashboard():return call('GET','/api/demo')


def main():
    health=call('GET','/api/health')
    call('GET','/api/ready')
    booking_path=cloud.PRIVATE/'cloud-acceptance-booking.json'
    if not booking_path.exists():
        # Explicit recipient comes only from ignored private configuration.
        recipient=json.loads((cloud.PRIVATE/'test-recipient.json').read_text())['email']
        session=call('POST','/api/admin/session',admin=True)
        cloud.save('cloud-acceptance-booking',{'booking_id':str(uuid.uuid4()),'restaurant_id':'aurora',
            'nickname':'Verifica demo cloud','email':recipient,'party_size':2,'reservation_time':'20:00'})
        cloud.save('cloud-acceptance-session',session)
    booking=json.loads(booking_path.read_text())
    path='/api/bookings/'+booking['booking_id']
    existing=requests.get(base+path,timeout=30)
    submitted=existing.status_code==200 and existing.json()['notification']['notification_state']=='SUBMITTED'
    if not submitted:
        call('POST','/api/admin/notifications',{'paused':True},admin=True)
        try:
            until(lambda:next(iter(sorted((w for w in dashboard()['workers'] if w['component']=='notifications'),key=lambda w:w['last_seen_at'],reverse=True)),{}).get('worker_state')=='PAUSED')
            call('POST','/api/bookings',booking)
            until(lambda:call('GET',path)['booking_state']=='CONFIRMED')
            assert call('GET',path)['notification']['attempt_count']==0
            until(lambda:((dashboard()['kafka'] or {}).get('notifications',{}).get('lag') or 0)>=1)
            print('PASS: HTTPS booking confirmed while notification consumer is paused; real Kafka lag visible',flush=True)
        finally:
            call('POST','/api/admin/notifications',{'paused':False},admin=True)
    until(lambda:call('GET',path)['notification']['notification_state']=='SUBMITTED')
    until(lambda:(dashboard()['kafka'] or {}).get('notifications',{}).get('lag')==0)
    before=call('GET',path)
    # Concurrent retries reuse exactly the same idempotency UUID.
    with ThreadPoolExecutor(max_workers=10) as pool:
        answers=list(pool.map(lambda _:call('POST','/api/bookings',booking),range(30)))
    assert all(a['public_alias']==before['public_alias'] for a in answers)
    assert call('GET',path)['notification']['attempt_count']==1
    request=call('POST','/api/admin/replay',admin=True)
    replay=until(lambda:next((r for r in dashboard()['replays'] if r['run_id']==request['run_id'] and r['run_state'] in ['COMPLETE','FAILED']),None))
    assert replay['run_state']=='COMPLETE',replay
    expected=json.loads(replay['expected_json']) if isinstance(replay['expected_json'],str) else replay['expected_json']
    actual=json.loads(replay['actual_json']) if isinstance(replay['actual_json'],str) else replay['actual_json']
    assert replay['records_processed']==sum(expected.values()) and expected==actual
    public=dashboard()
    text=json.dumps(public)
    assert booking['email'] not in text and booking['nickname'] not in text
    assert call('GET',path)['notification']['attempt_count']==1
    result={'https':'verified','release':health['release'],'booking_id':booking['booking_id'],
        'booking':'CONFIRMED','smtp':'SUBMITTED','attempts':1,'concurrent_idempotent_retries':30,
        'replay':f'equal, {replay["records_processed"]} unique bookings at captured offsets',
        'public_privacy':'passed','mailbox_delivery':'verify separately'}
    cloud.save('cloud-acceptance-result',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
