"""Durable consumer effects. Kafka offsets are committed only after these transactions."""
from .events import insert_outbox, new_event
from .runtime import execute, log, one


def deduplicated(db, role, projection, event, source):
    if one(db, '''SELECT event_id FROM LT_OWNER.processed_events
        WHERE processor_name=:b_role AND projection_id=:b_projection AND event_id=:b_event''',
        b_role=role, b_projection=projection, b_event=event['event_id']):
        return True
    execute(db, '''INSERT INTO LT_OWNER.processed_events
        (processor_name,projection_id,event_id,session_id,restaurant_id,source_topic,partition_id,source_offset)
        VALUES(:b_role,:b_projection,:b_event,:b_session,:b_restaurant,:b_topic,:b_partition,:b_offset)''',
        b_role=role, b_projection=projection, b_event=event['event_id'], b_session=event['session_id'],
        b_restaurant=event['restaurant_id'], b_topic=source['topic'], b_partition=source['partition'], b_offset=source['offset'])
    return False


def confirm(db, event, source):
    # The row lock serializes a redelivery with an in-flight confirmation.
    booking = one(db, 'SELECT booking_state FROM LT_OWNER.bookings WHERE booking_id=:b_id FOR UPDATE', b_id=event['booking_id'])
    if not booking:
        raise ValueError('BOOKING_NOT_FOUND')
    if deduplicated(db, 'confirmation', 'live', event, source):
        db.rollback()
        log(db, event, 'confirmation', 'event.duplicate_skipped', detail='Evento già elaborato; nessun effetto ripetuto', source=source)
        db.commit()
        return 'duplicate'
    # A created event only acknowledges intake. Only an authenticated admin
    # decision may change state and generate a status_changed notification.
    log(db, event, 'confirmation', 'booking.awaiting_decision',
        detail='Richiesta ricevuta; decisione amministratore richiesta', source=source)
    db.commit()
    return 'awaiting_decision'



def project(db, event, source, projection='live'):
    if deduplicated(db, 'trending', projection, event, source):
        db.rollback()
        log(db, event, 'trending', 'event.duplicate_skipped', detail='Evento già presente nella proiezione; nessun incremento', source=source)
        db.commit()
        return 'duplicate'
    execute(db, '''MERGE INTO LT_OWNER.restaurant_stats s USING
        (SELECT :b_session session_id,:b_projection projection_id,:b_restaurant restaurant_id FROM dual) e
        ON (s.session_id=e.session_id AND s.projection_id=e.projection_id AND s.restaurant_id=e.restaurant_id)
        WHEN MATCHED THEN UPDATE SET s.booking_count=s.booking_count+1,s.updated_at=SYSTIMESTAMP
        WHEN NOT MATCHED THEN INSERT(session_id,projection_id,restaurant_id,booking_count) VALUES(e.session_id,e.projection_id,e.restaurant_id,1)''',
        b_session=event['session_id'], b_projection=projection, b_restaurant=event['restaurant_id'])
    log(db, event, 'trending', 'ranking.updated' if projection=='live' else 'ranking.replayed',
        detail='Proiezione '+projection, source=source)
    db.commit()
    return 'counted'
