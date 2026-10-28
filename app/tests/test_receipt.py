import unittest
import uuid
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pydantic import ValidationError
from lasttable import api, notification
from lasttable.events import BookingEvent, new_event, insert_outbox, CHANGED

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.booking={'booking_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),'restaurant_id':'aurora','public_alias':'LT-123456ABCDEF'}
        self.event=new_event(self.booking,'booking.received')
        self.recipient={**self.booking,'recipient_email':'fixture@example.com','nickname':'Cliente','party_size':2,
            'restaurant_name':'Aurora','reservation_time':'20:00'}
        self.db=MagicMock()
        @contextmanager
        def database(*args):yield self.db
        self.database=database

    def test_receipt_contract_and_notifications_topic(self):
        self.assertEqual((self.event['state'],self.event['state_version']),('PENDING',0))
        self.assertNotIn('recipient_email',self.event)
        self.assertNotIn('nickname',self.event)
        for state in ['CONFIRMED','REJECTED']:
            with self.assertRaises(ValidationError):BookingEvent.model_validate({**self.event,'state':state})
        with patch('lasttable.runtime.execute') as write:
            insert_outbox(self.db,self.event)
        self.assertEqual(write.call_args.kwargs['b_topic'],CHANGED)
        decision=new_event({**self.booking,'state':'CONFIRMED'},'booking.status_changed')
        self.assertNotEqual(self.event['event_id'],decision['event_id'])

    def test_booking_queues_receipt_and_intake_in_same_transaction(self):
        body=api.Reservation(booking_id=self.booking['booking_id'],restaurant_id='aurora',nickname='Cliente',
            email='fixture@example.com',party_size=2,reservation_time='20:00')
        request=SimpleNamespace(client=SimpleNamespace(host='receipt-probe'))
        with patch.object(api,'database',self.database),patch.object(api,'rate_limit'), \
             patch.object(api,'current_session',return_value={**self.booking,'accepting':1}), \
             patch.object(api,'one',side_effect=[None,{'restaurant_id':'aurora'},{'total':0}]), \
             patch.object(api,'execute'),patch.object(api,'log'),patch.object(api,'booking_status',return_value={'booking_state':'PENDING'}), \
             patch.object(api,'insert_outbox') as outbox:
            self.assertEqual(api.reserve(body,request)['booking_state'],'PENDING')
        events=[c.args[1] for c in outbox.call_args_list]
        self.assertEqual([e['event_type'] for e in events],['booking.created','booking.received'])
        self.assertEqual(events[0]['booking_id'],events[1]['booking_id'])
        self.db.commit.assert_called_once()

    def test_http_retry_does_not_queue_another_receipt(self):
        body=api.Reservation(booking_id=self.booking['booking_id'],restaurant_id='aurora',nickname='Cliente',
            email='fixture@example.com',party_size=2,reservation_time='20:00')
        with patch.object(api,'database',self.database),patch.object(api,'rate_limit'), \
             patch.object(api,'current_session',return_value={**self.booking,'accepting':1}), \
             patch.object(api,'one',return_value=self.booking),patch.object(api,'booking_status',return_value={}), \
             patch.object(api,'insert_outbox') as outbox:
            api.reserve(body,SimpleNamespace())
        outbox.assert_not_called()

    def test_receipt_email_explicitly_waits_for_confirmation(self):
        smtp=MagicMock()
        smtp.mail.return_value=(250,b'OK');smtp.rcpt.return_value=(250,b'OK');smtp.data.return_value=(250,b'OK')
        with patch.object(notification,'database',self.database), \
             patch.object(notification,'one',side_effect=[self.recipient,{'notification_state':'PENDING','attempt_count':0,'busy':0}]), \
             patch.object(notification,'execute',return_value=1),patch.object(notification,'log'), \
             patch.object(notification,'config',return_value={'sender':'demo@example.com','smtp_host':'capture','smtp_local_capture':True}), \
             patch.object(notification.smtplib,'SMTP',return_value=smtp):
            self.assertEqual(notification.notify(self.event),{'state':'SUBMITTED','attempts':1})
        message=BytesParser(policy=default).parsebytes(smtp.data.call_args.args[0])
        self.assertIn('presa in carico',message['Subject'])
        self.assertIn('in attesa di conferma',message['Subject'])
        self.assertIn('non è ancora confermato',message.get_body(preferencelist=('plain',)).get_content())
        smtp.data.assert_called_once()

    def test_receipt_redelivery_does_not_send_again(self):
        with patch.object(notification,'database',self.database), \
             patch.object(notification,'one',side_effect=[self.recipient,{'notification_state':'SUBMITTED','attempt_count':1,'busy':0}]), \
             patch.object(notification,'execute'),patch.object(notification.smtplib,'SMTP') as smtp:
            self.assertEqual(notification.notify(self.event),{'state':'SUBMITTED','attempts':1})
        smtp.assert_not_called()

if __name__=='__main__':unittest.main()
