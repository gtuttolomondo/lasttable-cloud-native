"""Versioned, PII-free Kafka contract shared by publishers and consumers."""
import json
import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CREATED = 'lasttable.bookings.created'
CHANGED = 'lasttable.bookings.status-changed'


class BookingEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    event_id: uuid.UUID
    event_type: Literal['booking.created', 'booking.received', 'booking.status_changed']
    booking_id: uuid.UUID
    public_alias: str = Field(pattern=r'^LT-[A-F0-9]{12}$')
    session_id: uuid.UUID
    restaurant_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,31}$')
    correlation_id: uuid.UUID
    state_version: int = Field(ge=0, le=1)
    state: Literal['PENDING', 'CONFIRMED', 'REJECTED']
    occurred_at: datetime

    @model_validator(mode='after')
    def coherent_transition(self):
        valid = (self.state, self.state_version) == ('PENDING', 0) if self.event_type in ('booking.created','booking.received') else self.state in ('CONFIRMED', 'REJECTED') and self.state_version == 1
        if not valid or self.occurred_at.tzinfo is None:
            raise ValueError('Incoherent state transition or missing timezone')
        return self

    def wire(self):
        return self.model_dump(mode='json')


def new_event(booking, event_type):
    changed = event_type == 'booking.status_changed'
    return BookingEvent(event_id=uuid.uuid4(), event_type=event_type,
        booking_id=booking['booking_id'], public_alias=booking['public_alias'],
        session_id=booking['session_id'], restaurant_id=booking['restaurant_id'],
        correlation_id=booking.get('correlation_id', booking['booking_id']),
        state_version=1 if changed else 0, state=(booking.get('state') if booking.get('state') in ('CONFIRMED','REJECTED') else 'CONFIRMED') if changed else 'PENDING',
        occurred_at=datetime.now(timezone.utc)).wire()


def insert_outbox(connection, event):
    from .runtime import execute
    execute(connection, '''INSERT INTO LT_OWNER.outbox_events
        (event_id,aggregate_id,aggregate_version,event_type,topic,payload,session_id,correlation_id)
        VALUES(:b_event,:b_booking,:b_version,:b_type,:b_topic,:b_payload,:b_session,:b_correlation)''',
        b_event=event['event_id'], b_booking=event['booking_id'], b_version=event['state_version'],
        b_type=event['event_type'], b_topic=CREATED if event['event_type']=='booking.created' else CHANGED,
        b_payload=json.dumps(event), b_session=event['session_id'], b_correlation=event['correlation_id'])
