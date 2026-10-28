"""Real Oracle admin flow probe, rolled back with no emails or public test data.

Run inside the released API pod. Uses its restricted database identity and
actual HTTP routes. Commit calls are held within one test transaction, whose
rows and outbox events are always rolled back before Kafka can see them.
"""
import os
import uuid
from contextlib import contextmanager
from unittest.mock import patch
from fastapi.testclient import TestClient
from lasttable import api, processing
from lasttable.runtime import database, rows

code=os.environ['LASTTABLE_TABLE_ADMIN_CODE']
client=TestClient(api.app)
headers={'Authorization':'Bearer '+code}
sid=str(uuid.uuid4())
rid='probe-'+uuid.uuid4().hex[:12]

class Transaction:
    def __init__(self,db):self.db=db
    def cursor(self):return self.db.cursor()
    def commit(self):pass
    def rollback(self):self.db.rollback()

with database('api') as db:
    proxy=Transaction(db)
    @contextmanager
    def fixture(*args):yield proxy
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO LT_OWNER.demo_sessions(session_id,title,accepting) VALUES(:sid,'Admin probe',1)",sid=sid)
    with patch.object(api,'database',fixture),patch.object(api,'current_session',return_value={'session_id':sid,'accepting':1}):
        # Avoid READ ONLY inside this intentionally uncommitted fixture transaction.
        original_execute=api.execute
        def execute(conn,sql,**binds):
            return None if sql=='SET TRANSACTION READ ONLY' else original_execute(conn,sql,**binds)
        with patch.object(api,'execute',execute):
            assert client.get('/api/admin/bookings').status_code==401
            restaurant={'restaurant_id':rid,'name':'Prova gestione','cuisine':'Italiana','area':'Milano',
                'description':'Ristorante di prova transazionale','icon':'sun','price_label':'€€'}
            assert client.post('/api/admin/restaurants',headers=headers,json=restaurant).status_code==201
            edited={k:v for k,v in restaurant.items() if k!='restaurant_id'};edited['name']='Modifica verificata'
            assert client.put('/api/admin/restaurants/'+rid,headers=headers,json=edited).status_code==200
            ids=[]
            for i in range(4):
                identifier=str(uuid.uuid4());ids.append(identifier)
                answer=client.post('/api/bookings',json={'booking_id':identifier,'restaurant_id':rid,'nickname':'Fixture privata',
                    'email':'fixture@example.com','party_size':i+1,'reservation_time':'20:00'})
                assert answer.status_code==202,answer.text
                assert answer.json()['booking_state']=='PENDING'
            listing=client.get('/api/admin/bookings',headers=headers)
            assert listing.status_code==200,listing.text
            assert len(listing.json()['bookings'])==4
            for identifier in ids:
                receipt=client.get('/api/bookings/'+identifier).json()
                assert receipt['acknowledgement']['notification_state']=='PENDING'
                assert receipt['booking_state']=='PENDING'
            receipts=rows(db,"SELECT event_id FROM LT_OWNER.outbox_events WHERE session_id=:sid AND event_type='booking.received'",sid=sid)
            assert len(receipts)==4
            assert listing.headers['cache-control']=='no-store'
            def decide(state,ids=None,all_pending=False):
                answer=client.post('/api/admin/bookings/decision',headers=headers,json={'state':state,'booking_ids':ids or [],'all_pending':all_pending})
                assert answer.status_code==200,answer.text
                return answer.json()
            assert len(decide('CONFIRMED',[ids[0]])['updated'])==1
            assert len(decide('REJECTED',[ids[0]])['skipped'])==1
            assert len(decide('REJECTED',ids[1:3])['updated'])==2
            assert len(decide('CONFIRMED',all_pending=True)['updated'])==1
            assert not decide('REJECTED',all_pending=True)['updated']
            events=rows(db,"SELECT payload FROM LT_OWNER.outbox_events WHERE session_id=:sid AND event_type='booking.status_changed'",sid=sid)
            assert len(events)==4
            status={b['booking_id']:b['booking_state'] for b in client.get('/api/admin/bookings',headers=headers).json()['bookings']}
            assert [status[i] for i in ids]==['CONFIRMED','REJECTED','REJECTED','CONFIRMED']
    db.rollback()
    assert not rows(db,'SELECT restaurant_id FROM LT_OWNER.restaurants WHERE restaurant_id=:id',id=rid)
print('PASS: real Oracle create/edit restaurant, manual pending, single and bulk decisions, terminal-state protection, one outbox event per decision, protected API, rollback with no emails')
