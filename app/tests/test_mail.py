import unittest
import uuid
from lasttable.mail import booking_message
from lasttable.events import new_event

class MailTests(unittest.TestCase):
    def test_all_templates_have_html_and_plain_text(self):
        booking={'booking_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),'restaurant_id':'aurora','public_alias':'LT-123456ABCDEF'}
        recipient={**booking,'nickname':'Giovanni','restaurant_name':'Aurora','party_size':3,'reservation_time':'19:30','recipient_email':'fixture@example.com'}
        for state,phrase in [('PENDING','in attesa di conferma'),('CONFIRMED','confermata'),('REJECTED','rifiutata')]:
            event=new_event({**booking,'state':state},'booking.received' if state=='PENDING' else 'booking.status_changed')
            message=booking_message(recipient,event,'demo@example.com')
            self.assertEqual(message.get_content_type(),'multipart/alternative')
            self.assertIn(phrase,message['Subject'])
            plain=message.get_body(preferencelist=('plain',)).get_content()
            html=message.get_body(preferencelist=('html',)).get_content()
            self.assertIn('Aurora',plain)
            for value in ['Aurora','19:30','LT-123456ABCDEF','lasttable','#183d34']:
                self.assertIn(value,html)
            self.assertNotIn('<script',html)
            self.assertNotIn('<img',html)

    def test_personal_and_catalog_fields_are_escaped(self):
        recipient={'nickname':'<script>alert(1)</script>','restaurant_name':'Aurora & <b>amici</b>',
            'party_size':2,'reservation_time':'20:00','public_alias':'LT-123456ABCDEF','recipient_email':'fixture@example.com'}
        event={'state':'PENDING','event_id':str(uuid.uuid4())}
        html=booking_message(recipient,event,'demo@example.com').get_body(preferencelist=('html',)).get_content()
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('Aurora &amp; &lt;b&gt;amici&lt;/b&gt;',html)

if __name__=='__main__':unittest.main()
