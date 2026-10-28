"""Role-based workers; each process has its own Kafka credential and group."""
import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone

import httpx
import oci
from confluent_kafka import Consumer, KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient
from pydantic import ValidationError

from .events import BookingEvent, CREATED, CHANGED
from .processing import confirm, project
from .observability import TOPICS, GROUPS, position_metrics
from .runtime import config, current_session, database, execute, kafka_settings, log, one, rows, signer

STOP = False


def stop(*_):
    global STOP
    STOP = True


def heartbeat(db, role, state='RUNNING'):
    execute(db, '''MERGE INTO LT_OWNER.worker_status w USING (SELECT :b_id worker_id FROM dual) s
        ON(w.worker_id=s.worker_id) WHEN MATCHED THEN UPDATE SET w.worker_state=:b_state,w.last_seen_at=SYSTIMESTAMP
        WHEN NOT MATCHED THEN INSERT(worker_id,component,worker_state) VALUES(:b_id,:b_component,:b_state)''',
        b_id=role+'-'+os.getenv('HOSTNAME', 'local'), b_component=role, b_state=state)


def produce(producer, topic, key, value):
    delivered = []
    producer.produce(topic, key=key.encode(), value=json.dumps(value).encode(),
        on_delivery=lambda error, message: delivered.append((error, message)))
    if producer.flush(20) or not delivered:
        raise TimeoutError('KAFKA_ACK_TIMEOUT')
    error, message = delivered[0]
    if error:
        raise KafkaException(error)
    return message


def publisher():
    producer = Producer({**kafka_settings('producer'), 'enable.idempotence': True, 'message.timeout.ms': 15000})
    while not STOP:
        with database() as db:
            heartbeat(db, 'publisher')
            with db.cursor() as cursor:
                cursor.prefetchrows = 0
                cursor.arraysize = 1
                cursor.execute('''SELECT event_id,topic,payload FROM LT_OWNER.outbox_events
                    WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED''')
                row = cursor.fetchone()
            if row:
                # Oracle 23+ can return a CLOB with IS JSON as an already decoded dict.
                event = (BookingEvent.model_validate(row[2]) if isinstance(row[2], dict)
                         else BookingEvent.model_validate_json(row[2])).wire()
                message = produce(producer, row[1], event['booking_id'], event)
                execute(db, '''UPDATE LT_OWNER.outbox_events SET published_at=SYSTIMESTAMP,
                    partition_id=:b_partition,source_offset=:b_offset WHERE event_id=:b_event''',
                    b_partition=message.partition(), b_offset=message.offset(), b_event=row[0])
                log(db, event, 'publisher', 'event.published', detail='ACK del broker ricevuto prima di marcare l’outbox',
                    source={'topic': row[1], 'partition': message.partition(), 'offset': message.offset()})
            db.commit()
        if not row:
            time.sleep(0.5)


def invoke_notification(event):
    cfg = config()
    if cfg.get('local_function_url'):
        response = httpx.post(cfg['local_function_url'], json=event, timeout=40)
        response.raise_for_status()
        return response.json()
    client = oci.functions.FunctionsInvokeClient({'region': cfg['region']}, signer=signer(),
        # The Function has a 60-second execution limit; leave room for its cold
        # start rather than abandoning a still-running invocation after 40s.
        service_endpoint=cfg['function_endpoint'], timeout=(5, 90), retry_strategy=oci.retry.NoneRetryStrategy())
    response = client.invoke_function(cfg['function_id'], invoke_function_body=json.dumps(event), fn_invoke_type='sync')
    return json.loads(response.data.content)


def notification(event):
    answer = invoke_notification(event)
    if answer['state'] == 'PENDING' or (answer['state']=='FAILED' and answer.get('attempts', 0)<3):
        raise RuntimeError('NOTIFICATION_RETRYABLE')
    return answer['state']


def consume(role):
    topic = CHANGED if role=='notifications' else CREATED
    group = 'lasttable-'+role
    consumer = Consumer({**kafka_settings(role), 'group.id': group, 'auto.offset.reset': 'earliest',
        'enable.auto.commit': False, 'enable.auto.offset.store': False, 'max.poll.interval.ms': 300000})
    producer = Producer({**kafka_settings(role), 'enable.idempotence': True, 'message.timeout.ms': 15000})
    paused = False
    last_heartbeat = 0
    def assigned(client, partitions):
        offsets = client.committed(partitions, timeout=10)
        initialize = []
        for p in offsets:
            if p.offset < 0:
                low, _ = client.get_watermark_offsets(p, timeout=10)
                initialize.append(TopicPartition(p.topic, p.partition, low))
        # Persist the starting position even for empty partitions. Offset 0 means
        # "next record to process", and does not acknowledge an unprocessed record.
        if initialize:
            client.commit(offsets=initialize, asynchronous=False)
        client.assign(partitions)
        if paused:
            client.pause(partitions)
    consumer.subscribe([topic], on_assign=assigned)
    try:
        while not STOP:
            if time.monotonic()-last_heartbeat > 2:
                with database() as db:
                    request_pause = False
                    if role=='notifications':
                        control = one(db, '''SELECT notifications_pause_requested FROM LT_OWNER.demo_controls
                            WHERE session_id=:b_session''', b_session=current_session(db)['session_id'])
                        request_pause = bool(control['notifications_pause_requested'])
                    assignment = consumer.assignment()
                    if assignment:
                        if request_pause:
                            consumer.pause(assignment)
                        elif paused:
                            consumer.resume(assignment)
                    paused = request_pause
                    heartbeat(db, role, 'PAUSED' if paused else 'RUNNING')
                    db.commit()
                last_heartbeat = time.monotonic()
            message = consumer.poll(0.5)
            if message is None:
                continue
            if message.error():
                raise KafkaException(message.error())
            # A rebalance/poll can deliver one buffered record while pause takes effect.
            # Seek it back so no invocation starts after the pause request is acknowledged.
            if paused:
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))
                consumer.pause(consumer.assignment())
                continue
            source = {'topic': message.topic(), 'partition': message.partition(), 'offset': message.offset(), 'group': group}
            try:
                event = BookingEvent.model_validate_json(message.value()).wire()
                expected = ('booking.received','booking.status_changed') if role=='notifications' else ('booking.created',)
                if event['event_type'] not in expected:
                    raise ValueError('WRONG_EVENT_TYPE')
            except (ValidationError, ValueError, TypeError):
                # No raw input is copied into the DLQ or public logs.
                produce(producer, 'lasttable.notifications.dlq' if role=='notifications' else 'lasttable.bookings.dlq',
                    str(message.partition()), {'schema_version': 1, 'error': 'INVALID_EVENT', 'source': source})
                consumer.commit(message=message, asynchronous=False)
                continue
            while not STOP:
                try:
                    if role=='notifications':
                        outcome = notification(event)
                        if outcome == 'FAILED':
                            produce(producer, 'lasttable.notifications.dlq', event['booking_id'],
                                {'schema_version': 1, 'error': 'SMTP_ATTEMPTS_EXHAUSTED', 'event_id': event['event_id'], 'source': source})
                        with database() as db:
                            log(db, event, role, 'function.completed', outcome=outcome, source=source)
                            db.commit()
                    else:
                        with database() as db:
                            (confirm if role=='confirmation' else project)(db, event, source)
                    consumer.commit(message=message, asynchronous=False)
                    break
                except KafkaException:
                    # A failed commit after a rebalance requires a new group join.
                    # Recreate the consumer; durable effects tolerate redelivery.
                    raise
                except Exception as error:
                    # Redelivery remains possible. Never commit a transient failure.
                    print(json.dumps({'component': role, 'error_type': type(error).__name__, 'action': 'retry'}), flush=True)
                    time.sleep(3)
    finally:
        consumer.close()


def observer():
    settings = kafka_settings('observer')
    admin = AdminClient(settings)
    probe = Consumer({**settings, 'group.id':'lasttable-observer', 'enable.auto.commit':False})
    previous_topics = {}
    topic_refresh = 0
    try:
        while not STOP:
            snapshot, topics, group_states = {}, {}, {}
            metadata = admin.list_topics(timeout=8)
            for topic in TOPICS:
                info = metadata.topics.get(topic)
                if not info or info.error:
                    topics[topic] = {'available':False,'partitions':[],'error':'TOPIC_UNAVAILABLE'}
                    continue
                try:
                    partitions = []
                    for partition in sorted(info.partitions):
                        low, high = probe.get_watermark_offsets(TopicPartition(topic,partition),timeout=5,cached=False)
                        partitions.append({'id':partition,'low':low,'high':high})
                    topics[topic] = {'available':True,'partitions':partitions}
                except KafkaException:
                    topics[topic] = {'available':False,'partitions':[],'error':'WATERMARK_UNAVAILABLE'}
            try:
                descriptions = admin.describe_consumer_groups(['lasttable-'+role for role in GROUPS],request_timeout=8)
                for role in GROUPS:
                    try:
                        description = descriptions['lasttable-'+role].result(timeout=9)
                        state = getattr(description.state,'name',str(description.state))
                        group_states[role] = {'members':len(description.members),'state':state}
                    except Exception:
                        group_states[role] = {'members':None,'state':'UNKNOWN'}
            except Exception:
                group_states = {role:{'members':None,'state':'UNKNOWN'} for role in GROUPS}
            for role, topic in GROUPS.items():
                client = Consumer({**settings,'group.id':'lasttable-'+role,'enable.auto.commit':False})
                try:
                    measured = topics[topic]
                    if not measured['available']:
                        raise ValueError('TOPIC_UNAVAILABLE')
                    parts = measured['partitions']
                    positions = client.committed([TopicPartition(topic,p['id']) for p in parts],timeout=5)
                    # Lag is calculated independently for every partition.
                    snapshot[role] = position_metrics(parts, positions)
                except (KafkaException,ValueError):
                    snapshot[role] = {'lag':None,'committed':None,'end':None,'error':'UNAVAILABLE'}
                finally:
                    client.close()
            with database() as db:
                heartbeat(db,'observer')
                event = {'session_id':current_session(db)['session_id']}
                log(db,event,'observer','kafka.snapshot',detail=json.dumps(snapshot,separators=(',',':')))
                refresh_topics = time.monotonic()-topic_refresh >= 600
                for topic, value in topics.items():
                    if refresh_topics or previous_topics.get(topic)!=value:
                        log(db,event,'observer','kafka.topic.snapshot',detail=json.dumps(value,separators=(',',':')),source={'topic':topic})
                previous_topics = topics
                if refresh_topics:
                    topic_refresh = time.monotonic()
                inventory = {'brokers':len(metadata.brokers),'observed_topics':sum(t['available'] for t in topics.values()),
                    'partitions':sum(len(t['partitions']) for t in topics.values()),'groups':group_states}
                log(db,event,'observer','kafka.inventory.snapshot',detail=json.dumps(inventory,separators=(',',':')))
                db.commit()
            time.sleep(10)
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['publisher', 'confirmation', 'trending', 'notifications', 'observer', 'replay'])
    role = parser.parse_args().role
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not STOP:
        try:
            if role=='publisher': publisher()
            elif role=='observer': observer()
            elif role=='replay':
                from .replay import run
                run(lambda: STOP)
            else: consume(role)
        except Exception as error:
            print(json.dumps({'component': role, 'error_type': type(error).__name__, 'action': 'restart_loop'}), flush=True)
            time.sleep(3)


if __name__ == '__main__':
    main()
