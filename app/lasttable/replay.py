"""Rebuild an isolated ranking from a fixed Kafka high-watermark snapshot."""
import json
import time
from confluent_kafka import Consumer, KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient
from pydantic import ValidationError
from .events import BookingEvent, CREATED
from .processing import project
from .runtime import current_session, database, execute, kafka_settings, log, one, rows


def replay_event(value, session_id):
    # Live consumers have already routed malformed records to their DLQ.
    # A retained bad record must not prevent rebuilding valid booking history.
    try:
        event = BookingEvent.model_validate_json(value).wire()
    except (ValidationError, ValueError, TypeError):
        return None
    return event if event['event_type']=='booking.created' and event['session_id']==session_id else None


def replay(request, stopped):
    cfg = kafka_settings('trending')
    admin = AdminClient(cfg)
    consumer = Consumer({**cfg, 'group.id': request['group_id'], 'enable.auto.commit': False,
        'enable.auto.offset.store': False, 'auto.offset.reset': 'earliest'})
    live = Consumer({**cfg, 'group.id': 'lasttable-trending', 'enable.auto.commit': False})
    try:
        metadata = admin.list_topics(CREATED, timeout=10)
        partitions = [TopicPartition(CREATED, p) for p in metadata.topics[CREATED].partitions]
        bounds = {p.partition: consumer.get_watermark_offsets(p, timeout=10) for p in partitions}
        # A complete-history demonstration must never silently start after retention loss.
        if any(low != 0 for low, high in bounds.values()):
            raise ValueError('RETENTION_COVERAGE_UNAVAILABLE')
        cutoffs = {p: high for p, (low, high) in bounds.items()}
        deadline = time.monotonic()+120
        while any(p.offset < cutoffs[p.partition] for p in live.committed(partitions, timeout=10)):
            if time.monotonic() > deadline or stopped():
                raise TimeoutError('LIVE_PROJECTION_NOT_CAUGHT_UP')
            time.sleep(1)
        with database() as db:
            records = rows(db, '''SELECT restaurant_id,partition_id,source_offset FROM LT_OWNER.processed_events
                WHERE processor_name='trending' AND projection_id='live' AND session_id=:b_session''', b_session=request['session_id'])
            expected = {}
            for row in records:
                if row['source_offset'] < cutoffs[row['partition_id']]:
                    key = row['restaurant_id']
                    expected[key] = expected.get(key, 0)+1
            execute(db, 'UPDATE LT_OWNER.replay_runs SET cutoffs_json=:b_cutoffs,expected_json=:b_expected WHERE run_id=:b_id',
                b_cutoffs=json.dumps(cutoffs), b_expected=json.dumps(expected), b_id=request['run_id'])
            db.commit()
        active = {p for p, high in cutoffs.items() if high > 0}
        consumer.assign([TopicPartition(CREATED, p, 0) for p in active])
        processed = 0
        while active:
            if stopped() or time.monotonic() > deadline:
                raise TimeoutError('REPLAY_DEADLINE')
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                raise KafkaException(message.error())
            p = message.partition()
            if message.offset() < cutoffs[p]:
                event = replay_event(message.value(), request['session_id'])
                if event:
                    with database() as db:
                        outcome = project(db, event, {'topic': CREATED, 'partition': p, 'offset': message.offset(), 'group': request['group_id']}, request['projection_id'])
                        processed += outcome == 'counted'
                consumer.commit(message=message, asynchronous=False)
            if message.offset()+1 >= cutoffs[p]:
                active.discard(p)
                consumer.pause([TopicPartition(CREATED, p)])
        with database() as db:
            actual = {r['restaurant_id']: r['booking_count'] for r in rows(db,
                'SELECT restaurant_id,booking_count FROM LT_OWNER.restaurant_stats WHERE session_id=:b_session AND projection_id=:b_projection',
                b_session=request['session_id'], b_projection=request['projection_id'])}
            execute(db, '''UPDATE LT_OWNER.replay_runs SET run_state=:b_state,actual_json=:b_actual,
                records_processed=:b_count,completed_at=SYSTIMESTAMP,error_code=:b_error WHERE run_id=:b_id''',
                b_state='COMPLETE' if actual==expected else 'FAILED', b_actual=json.dumps(actual), b_count=processed,
                b_error=None if actual==expected else 'PROJECTION_MISMATCH', b_id=request['run_id'])
            log(db, {'session_id': request['session_id']}, 'replay', 'replay.completed',
                outcome='OK' if actual==expected else 'FAILED', detail=f'{processed} eventi unici; nessuna invocazione della Function')
            db.commit()
    finally:
        consumer.close()
        live.close()


def run(stopped):
    from .worker import heartbeat
    # This deployment has one replay worker with Recreate strategy. A process
    # restart cannot leave an old RUNNING request blocking future demonstrations.
    with database() as db:
        execute(db, "UPDATE LT_OWNER.replay_runs SET run_state='FAILED',completed_at=SYSTIMESTAMP,error_code='WORKER_INTERRUPTED' WHERE run_state='RUNNING'")
        db.commit()
    while not stopped():
        with database() as db:
            heartbeat(db, 'replay')
            with db.cursor() as cursor:
                cursor.prefetchrows = 0
                cursor.arraysize = 1
                cursor.execute("SELECT run_id,session_id,projection_id,group_id FROM LT_OWNER.replay_runs WHERE run_state='REQUESTED' ORDER BY requested_at FOR UPDATE SKIP LOCKED")
                row = cursor.fetchone()
            if row:
                request = dict(zip(['run_id', 'session_id', 'projection_id', 'group_id'], row))
                execute(db, "UPDATE LT_OWNER.replay_runs SET run_state='RUNNING' WHERE run_id=:b_id", b_id=row[0])
            db.commit()
        if not row:
            time.sleep(2)
            continue
        try:
            replay(request, stopped)
        except Exception as error:
            with database() as db:
                code = str(error) if isinstance(error, (ValueError, TimeoutError)) else type(error).__name__
                execute(db, "UPDATE LT_OWNER.replay_runs SET run_state='FAILED',completed_at=SYSTIMESTAMP,error_code=:b_error WHERE run_id=:b_id",
                    b_error=code[:100], b_id=request['run_id'])
                db.commit()
