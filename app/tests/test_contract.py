import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError
from lasttable.events import BookingEvent, new_event
from lasttable.replay import replay_event
from lasttable.api import RATE, Reservation, rate_limit


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.booking={'booking_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4()),
            'restaurant_id':'aurora','public_alias':'LT-012345ABCDEF',
            'recipient_email':'private@example.com','nickname':'Private participant'}

    def test_wire_roundtrip_excludes_personal_fields(self):
        event=new_event(self.booking,'booking.created')
        self.assertNotIn('recipient_email',event)
        self.assertNotIn('nickname',event)
        self.assertEqual(BookingEvent.model_validate(event).wire(),event)

    def test_schema_rejects_personal_fields_and_unknown_versions(self):
        event=new_event(self.booking,'booking.created')
        for extra in [{'email':'private@example.com'},{'nickname':'private'},{'schema_version':2}]:
            with self.assertRaises(ValidationError):BookingEvent.model_validate({**event,**extra})

    def test_confirmed_event_preserves_correlation_and_changes_identity(self):
        created=new_event(self.booking,'booking.created')
        changed=new_event(created,'booking.status_changed')
        self.assertEqual(changed['correlation_id'],created['correlation_id'])
        self.assertEqual(changed['booking_id'],created['booking_id'])
        self.assertNotEqual(changed['event_id'],created['event_id'])
        self.assertEqual((changed['state'],changed['state_version']),('CONFIRMED',1))

    def test_inconsistent_transition_rejected(self):
        event=new_event(self.booking,'booking.created')
        for patch_value in [{'state':'CONFIRMED'},{'state_version':1},{'occurred_at':'2026-10-27T10:00:00'}]:
            with self.assertRaises(ValidationError):BookingEvent.model_validate({**event,**patch_value})

    def test_replay_survives_dead_lettered_records(self):
        created=new_event(self.booking,'booking.created')
        session=created['session_id']
        self.assertEqual(replay_event(json.dumps(created),session),created)
        for value in ['invalid-json',json.dumps({**created,'schema_version':99}),
                      json.dumps(new_event(created,'booking.status_changed'))]:
            self.assertIsNone(replay_event(value,session))
        self.assertIsNone(replay_event(json.dumps(created),str(uuid.uuid4())))

    def test_request_limits_and_opaque_id(self):
        values={'booking_id':str(uuid.uuid4()),'restaurant_id':'aurora','nickname':'Demo',
            'email':'attendee@example.com','party_size':2,'reservation_time':'20:00'}
        for invalid in [{'booking_id':'predictable-id'},{'party_size':13},{'email':'invalid'},{'reservation_time':'04:99'}]:
            with self.assertRaises(ValidationError):Reservation.model_validate({**values,**invalid})

    def test_shared_ip_audience_and_rate_window(self):
        RATE.clear()
        request=SimpleNamespace(client=SimpleNamespace(host='192.0.2.5'))
        with patch('lasttable.api.time.monotonic',return_value=10):
            for _ in range(120):rate_limit(request)
            with self.assertRaises(HTTPException) as error:rate_limit(request)
            self.assertEqual(error.exception.status_code,429)
        with patch('lasttable.api.time.monotonic',return_value=71):rate_limit(request)
        RATE.clear()


if __name__=='__main__':unittest.main()
