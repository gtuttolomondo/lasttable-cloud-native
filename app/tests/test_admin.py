import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from lasttable import api, processing, notification
from lasttable.events import BookingEvent, new_event

@contextmanager
def fake_database(*args):
    yield MagicMock()

class AdminTests(unittest.TestCase):
    def setUp(self):
        self.booking={'booking_id':str(uuid.uuid4()),'public_alias':'LT-012345ABCDEF',
            'session_id':str(uuid.uuid4()),'restaurant_id':'new-restaurant','booking_state':'PENDING'}

    def test_admin_routes_require_authentication(self):
        with patch.dict('os.environ',{'LASTTABLE_TABLE_ADMIN_CODE':'test-only-code'}):
            client=TestClient(api.app)
            for method,path,payload in [('GET','/api/admin/bookings',None),
                ('POST','/api/admin/bookings/decision',{'state':'CONFIRMED','all_pending':True}),
                ('POST','/api/admin/restaurants',{}),('PUT','/api/admin/restaurants/test',{})]:
                answer=client.request(method,path,json=payload)
                self.assertEqual(answer.status_code,401)

    def test_intake_consumer_never_confirms(self):
        event=new_event(self.booking,'booking.created')
        with patch.object(processing,'one',return_value={'booking_state':'PENDING'}), \
             patch.object(processing,'deduplicated',return_value=False), \
             patch.object(processing,'execute') as write,patch.object(processing,'insert_outbox') as outbox, \
             patch.object(processing,'log'):
            result=processing.confirm(MagicMock(),event,{})
            self.assertEqual(result,'awaiting_decision')
            write.assert_not_called();outbox.assert_not_called()

    def test_dynamic_restaurant_and_rejection_event(self):
        created=new_event(self.booking,'booking.created')
        event=new_event({**created,'state':'REJECTED'},'booking.status_changed')
        self.assertEqual(BookingEvent.model_validate(event).state,'REJECTED')
        self.assertEqual(event['restaurant_id'],'new-restaurant')
        with self.assertRaises(ValidationError):BookingEvent.model_validate({**created,'state':'REJECTED'})

    def test_decision_skips_previous_final_state(self):
        for state in ['CONFIRMED','REJECTED']:
            with patch.object(api,'database',fake_database),patch.object(api,'current_session',return_value=self.booking), \
                 patch.object(api,'one',return_value={**self.booking,'booking_state':state}), \
                 patch.object(api,'execute') as write,patch.object(api,'insert_outbox') as outbox:
                answer=api.decide_bookings(api.BookingDecision(state='REJECTED',booking_ids=[self.booking['booking_id']]))
                self.assertEqual(answer['updated'],[])
                self.assertEqual(answer['skipped'],[self.booking['public_alias']])
                write.assert_not_called();outbox.assert_not_called()

    def test_decision_emits_one_event_even_for_repeated_identifiers(self):
        with patch.object(api,'database',fake_database),patch.object(api,'current_session',return_value=self.booking), \
             patch.object(api,'one',return_value=self.booking),patch.object(api,'execute') as write, \
             patch.object(api,'insert_outbox') as outbox,patch.object(api,'log'):
            answer=api.decide_bookings(api.BookingDecision(state='REJECTED',booking_ids=[self.booking['booking_id']]*2))
            self.assertEqual(len(answer['updated']),1)
            self.assertEqual(outbox.call_count,1)
            self.assertEqual(outbox.call_args.args[1]['state'],'REJECTED')
            self.assertEqual(write.call_count,1)

    def test_empty_or_ambiguous_bulk_decision_rejected(self):
        for values in [{},{'booking_ids':[self.booking['booking_id']],'all_pending':True}]:
            with self.assertRaises(HTTPException) as error:
                api.decide_bookings(api.BookingDecision(state='CONFIRMED',**values))
            self.assertEqual(error.exception.status_code,422)

    def test_rejection_notification_has_correct_email_content(self):
        from email.parser import BytesParser
        from email.policy import default
        event=new_event({**self.booking,'state':'REJECTED'},'booking.status_changed')
        recipient={'session_id':self.booking['session_id'],'recipient_email':'fixture@example.invalid',
            'public_alias':self.booking['public_alias'],'nickname':'Cliente','party_size':2,
            'restaurant_name':'Ristorante prova','reservation_time':'20:00'}
        smtp=MagicMock()
        smtp.mail.return_value=(250,b'OK');smtp.rcpt.return_value=(250,b'OK');smtp.data.return_value=(250,b'OK')
        with patch.object(notification,'database',fake_database), \
             patch.object(notification,'one',side_effect=[recipient,{'notification_state':'PENDING','attempt_count':0,'busy':0}]), \
             patch.object(notification,'execute',return_value=1),patch.object(notification,'log'), \
             patch.object(notification,'config',return_value={'sender':'demo@example.invalid','smtp_host':'capture','smtp_local_capture':True}), \
             patch.object(notification.smtplib,'SMTP',return_value=smtp):
            self.assertEqual(notification.notify(event),{'state':'SUBMITTED','attempts':1})
        message=BytesParser(policy=default).parsebytes(smtp.data.call_args.args[0])
        self.assertIn('rifiutata',str(message['Subject']))
        self.assertIn('non ha potuto accogliere',message.get_body(preferencelist=('plain',)).get_content())

    def test_restaurant_validation(self):
        values={'restaurant_id':'new-place','name':' Nuovo ristorante ','cuisine':'Italiana',
            'area':'Roma','description':'Nuova cucina','price_label':'€€','icon':'sun'}
        self.assertEqual(api.NewRestaurant(**values).name,'Nuovo ristorante')
        for extra in [{'restaurant_id':'../bad'},{'name':'  '},{'icon':'<script>'}]:
            with self.assertRaises(ValidationError):api.NewRestaurant(**{**values,**extra})

if __name__=='__main__':unittest.main()
