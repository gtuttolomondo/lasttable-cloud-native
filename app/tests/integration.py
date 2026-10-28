"""Local Podman acceptance tests against real Oracle transactions and Kafka.

SMTP is captured locally. This test refuses production messaging configuration.
Run in the backend image with the private development runtime mounts.
"""
import json
import os
import time
import uuid
from unittest.mock import patch

import httpx
from confluent_kafka import Producer
from lasttable.notification import notify
from lasttable.runtime import config, database, one, rows, secret
from lasttable.worker import produce
from lasttable.events import CREATED

assert config()['local_kafka'] and config()['smtp_local_capture']
http = httpx.Client(base_url='http://lasttable-api:8000', timeout=20, trust_env=False)
admin = {'Authorization': 'Bearer '+secret('demo-admin')}
table_admin = {'Authorization':'Bearer '+os.environ['LASTTABLE_TABLE_ADMIN_CODE']}


def get(path):
    answer = http.get(path)
    answer.raise_for_status()
    return answer.json()


def post(path, payload=None, privileged=False):
    answer = http.post(path, json=payload, headers=(table_admin if path=='/api/admin/bookings/decision' else admin) if privileged else {})
    answer.raise_for_status()
    return answer.json()


def until(check, timeout=90):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.7)
    raise AssertionError('Acceptance condition timed out')


def mail_count():
    response = httpx.get('http://lasttable-mailpit:8025/api/v1/messages', trust_env=False)
    response.raise_for_status()
    return response.json()['total']


def create(restaurant='aurora'):
    payload = {'booking_id': str(uuid.uuid4()), 'restaurant_id': restaurant, 'nickname': 'TEST PRIVATE NICKNAME',
               'email': 'attendee@example.com', 'party_size': 2, 'reservation_time': '20:00'}
    answer = post('/api/bookings', payload)
    assert answer['booking_state']=='PENDING'
    return payload


def status(booking):
    return get('/api/bookings/'+booking['booking_id'])


def state_event(booking):
    with database() as db:
        row = one(db, "SELECT payload FROM LT_OWNER.outbox_events WHERE aggregate_id=:b_id AND event_type='booking.status_changed'", b_id=booking['booking_id'])
        return row['payload'] if isinstance(row['payload'], dict) else json.loads(row['payload'])


post('/api/admin/session', privileged=True)
start_count = mail_count()
booking = create()
until(lambda: status(booking)['acknowledgement']['notification_state']=='SUBMITTED')
assert status(booking)['booking_state']=='PENDING' and mail_count()==start_count+1
post('/api/admin/bookings/decision',{'state':'CONFIRMED','booking_ids':[booking['booking_id']]},privileged=True)
until(lambda: status(booking)['notification']['notification_state']=='SUBMITTED')
assert status(booking)['booking_state']=='CONFIRMED'
assert mail_count()==start_count+2
assert post('/api/bookings', booking)['public_alias']==status(booking)['public_alias']
with database() as db:
    row = one(db, "SELECT payload FROM LT_OWNER.outbox_events WHERE aggregate_id=:b_id AND event_type='booking.created'", b_id=booking['booking_id'])
    event = row['payload'] if isinstance(row['payload'], dict) else json.loads(row['payload'])
producer = Producer({'bootstrap.servers': config()['kafka_bootstrap_servers'], 'enable.idempotence': True})
produce(producer, CREATED, event['booking_id'], event)
until(lambda: sum(r['booking_count'] for r in get('/api/demo')['ranking'])==1)
answer = httpx.post(config()['local_function_url'], json=state_event(booking), timeout=30, trust_env=False)
assert answer.json()['state']=='SUBMITTED'
assert mail_count()==start_count+2
print('PASS: booking + atomic outbox, duplicate HTTP/Kafka/invocation, one SMTP submission', flush=True)

post('/api/admin/notifications', {'paused': True}, privileged=True)
until(lambda: any(w['component']=='notifications' and w['worker_state']=='PAUSED' for w in get('/api/demo')['workers']))
unknown = create('del-porto')
retry = create('tavola-verde')
pending = create()
post('/api/admin/bookings/decision',{'state':'CONFIRMED','booking_ids':[b['booking_id'] for b in [unknown,retry,pending]]},privileged=True)
until(lambda: all(status(b)['booking_state']=='CONFIRMED' for b in [unknown, retry, pending]))
assert all(status(b)['notification']['attempt_count']==0 for b in [unknown, retry, pending])
until(lambda: ((get('/api/demo')['kafka'] or {}).get('notifications', {}).get('lag') or 0)>=3)
assert mail_count()==start_count+2
print('PASS: notification pause leaves confirmations running and produces visible consumer lag', flush=True)


class LostReply:
    def __init__(self, *args, **kwargs): pass
    def ehlo(self): pass
    def mail(self, *_): return 250, b'OK'
    def rcpt(self, *_): return 250, b'OK'
    def data(self, *_): raise TimeoutError('Injected lost final SMTP reply')
    def close(self): pass


with patch('lasttable.notification.smtplib.SMTP', LostReply):
    assert notify(state_event(unknown))['state']=='UNKNOWN'
assert notify(state_event(unknown))['state']=='UNKNOWN'
assert mail_count()==start_count+2
with patch('lasttable.notification.smtplib.SMTP', side_effect=ConnectionRefusedError('Injected setup failure')):
    assert notify(state_event(retry))=={'state': 'FAILED', 'attempts': 1}
assert notify(state_event(retry))=={'state': 'SUBMITTED', 'attempts': 2}
post('/api/admin/notifications', {'paused': False}, privileged=True)
until(lambda: status(pending)['notification']['notification_state']=='SUBMITTED')
until(lambda: get('/api/demo')['kafka']['notifications']['lag']==0)
assert mail_count()==start_count+7
assert status(unknown)['notification']['notification_state']=='UNKNOWN'
print('PASS: safe retry before DATA, persistent UNKNOWN after ambiguous DATA, no automatic resend', flush=True)

request = post('/api/admin/replay', privileged=True)
replay = until(lambda: next((r for r in get('/api/demo')['replays'] if r['run_id']==request['run_id'] and r['run_state'] in ('COMPLETE','FAILED')), None), timeout=150)
assert replay['run_state']=='COMPLETE', replay
assert replay['records_processed']==4
assert replay['expected_json']==replay['actual_json']
assert mail_count()==start_count+7
dashboard = get('/api/demo')
public = json.dumps(dashboard)
assert 'attendee@example.com' not in public and 'TEST PRIVATE NICKNAME' not in public
assert sum(r['booking_count'] for r in dashboard['ranking'])==4
print('PASS: isolated ranking replay matches four unique bookings; no additional emails; dashboard contains no recipient/nickname', flush=True)
print(json.dumps({'local_acceptance': 'passed', 'bookings': 4, 'new_smtp_submissions': 7,
    'unknown_notifications': 1, 'duplicate_processing': 'deduplicated', 'replay': 'equal', 'public_privacy': 'passed'}), flush=True)
