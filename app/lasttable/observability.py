"""Public operational views: explicit scope, logical event counts, no recipient data."""
import json
from datetime import datetime, timedelta, timezone
from .events import CREATED, CHANGED
from .runtime import rows, one

TOPICS = {CREATED: 'Richieste', CHANGED: 'Notifiche',
          'lasttable.bookings.dlq': 'Errori richieste', 'lasttable.notifications.dlq': 'Errori notifiche'}
GROUPS = {'confirmation': CREATED, 'trending': CREATED, 'notifications': CHANGED}


def decode(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def seconds(start, end):
    if not start or not end:
        return None
    def utc(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return round(max(0, (utc(end)-utc(start)).total_seconds()), 3)


def position_metrics(partitions, positions):
    by_id = {p.partition:p.offset for p in positions}
    known = all(by_id.get(p['id'], -1)>=0 for p in partitions)
    high = sum(p['high'] for p in partitions)
    committed = sum(by_id[p['id']] for p in partitions) if known else None
    return {'lag':sum(max(0,p['high']-by_id[p['id']]) for p in partitions) if known else None,
            'committed':committed,'end':high,
            'out_of_range':any(by_id.get(p['id'],-1)>=0 and not p['low']<=by_id[p['id']]<=p['high'] for p in partitions)}


def dashboard_metrics(db, sid):
    logical = rows(db, '''SELECT topic,COUNT(*) AS queued,
        SUM(CASE WHEN published_at IS NOT NULL THEN 1 ELSE 0 END) AS published,
        SUM(CASE WHEN published_at IS NULL THEN 1 ELSE 0 END) AS pending
        FROM LT_OWNER.outbox_events WHERE session_id=:sid GROUP BY topic''', sid=sid)
    effects = rows(db, '''SELECT processor_name,COUNT(*) AS processed FROM LT_OWNER.processed_events
        WHERE session_id=:sid AND projection_id='live' GROUP BY processor_name''', sid=sid)
    deliveries = rows(db, '''SELECT e.event_type,n.notification_state,COUNT(*) AS total,
        SUM(GREATEST(n.attempt_count-1,0)) AS retries
        FROM LT_OWNER.notification_delivery n JOIN LT_OWNER.outbox_events e ON e.event_id=n.event_id
        WHERE e.session_id=:sid GROUP BY e.event_type,n.notification_state''', sid=sid)
    duplicates = one(db, '''SELECT COUNT(*) AS total FROM LT_OWNER.demo_events WHERE session_id=:sid
        AND action='event.duplicate_skipped' ''', sid=sid)['total']
    recent = rows(db, '''SELECT public_alias,booking_state,created_at FROM LT_OWNER.bookings
        WHERE session_id=:sid ORDER BY created_at DESC FETCH FIRST 50 ROWS ONLY''', sid=sid)
    topic_samples = rows(db, '''SELECT topic,detail,occurred_at FROM (
        SELECT topic,detail,occurred_at,ROW_NUMBER() OVER(PARTITION BY topic ORDER BY log_id DESC) AS rn
        FROM LT_OWNER.demo_events WHERE session_id=:sid AND action='kafka.topic.snapshot') WHERE rn=1''', sid=sid)
    inventory = one(db, '''SELECT detail,occurred_at FROM LT_OWNER.demo_events WHERE session_id=:sid
        AND action='kafka.inventory.snapshot' ORDER BY log_id DESC FETCH FIRST 1 ROW ONLY''', sid=sid)
    history = rows(db, '''SELECT detail,occurred_at FROM LT_OWNER.demo_events WHERE session_id=:sid
        AND action='kafka.snapshot' ORDER BY log_id DESC FETCH FIRST 120 ROWS ONLY''', sid=sid)
    throughput = rows(db, '''SELECT minute,kind,COUNT(*) AS total FROM (
        SELECT TO_CHAR(SYS_EXTRACT_UTC(published_at),'YYYY-MM-DD"T"HH24:MI') AS minute,'published' AS kind
        FROM LT_OWNER.outbox_events WHERE session_id=:sid AND published_at>=SYSTIMESTAMP-INTERVAL '15' MINUTE
        UNION ALL
        SELECT TO_CHAR(SYS_EXTRACT_UTC(n.smtp_submitted_at),'YYYY-MM-DD"T"HH24:MI') AS minute,'smtp' AS kind
        FROM LT_OWNER.notification_delivery n JOIN LT_OWNER.outbox_events e ON e.event_id=n.event_id
        WHERE e.session_id=:sid AND n.smtp_submitted_at>=SYSTIMESTAMP-INTERVAL '15' MINUTE
        ) GROUP BY minute,kind ORDER BY minute''', sid=sid)
    samples = {row['topic']: {**decode(row['detail']), 'sampled_at': row['occurred_at']} for row in topic_samples}
    logical = {row['topic']: row for row in logical}
    topics = [{ 'name': name, 'label': label, 'is_dlq': name.endswith('.dlq'),
                'session': logical.get(name, {'queued':0,'published':0,'pending':0}),
                'cluster': samples.get(name)} for name,label in TOPICS.items()]
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    buckets = {(now-timedelta(minutes=i)).strftime('%Y-%m-%dT%H:%M'):{'at':(now-timedelta(minutes=i)).isoformat(),'published':0,'smtp':0} for i in range(14,-1,-1)}
    for point in throughput:
        if point['minute'] in buckets:
            buckets[point['minute']][point['kind']] = point['total']
    return {'topics':topics,'effects':effects,'deliveries':deliveries,'duplicate_skips':duplicates,
            'recent_bookings':recent,'throughput':list(buckets.values()),
            'inventory':{**decode(inventory['detail']),'sampled_at':inventory['occurred_at']} if inventory else None,
            'lag_history':[{'at':h['occurred_at'],'groups':decode(h['detail'])} for h in reversed(history)]}


def booking_trace(db, sid, alias):
    booking = one(db, '''SELECT booking_id,public_alias,booking_state,created_at FROM LT_OWNER.bookings
        WHERE session_id=:sid AND public_alias=:alias''', sid=sid, alias=alias)
    if not booking:
        return None
    identifier = booking.pop('booking_id')
    outbox = rows(db, '''SELECT e.event_id,e.event_type,e.topic,e.created_at,e.published_at,e.partition_id,e.source_offset,
        n.notification_state,n.attempt_count,n.error_code,n.smtp_submitted_at
        FROM LT_OWNER.outbox_events e LEFT JOIN LT_OWNER.notification_delivery n ON n.event_id=e.event_id
        WHERE e.aggregate_id=:id ORDER BY e.created_at''', id=identifier)
    effects = rows(db, '''SELECT processor_name,processed_at FROM LT_OWNER.processed_events
        WHERE session_id=:sid AND projection_id='live' AND event_id IN
        (SELECT event_id FROM LT_OWNER.outbox_events WHERE aggregate_id=:id)''', sid=sid, id=identifier)
    events = rows(db, '''SELECT log_id,event_id,component,action,outcome,detail,topic,group_id,partition_id,source_offset,occurred_at
        FROM LT_OWNER.demo_events WHERE session_id=:sid AND booking_id=:id
        ORDER BY log_id DESC FETCH FIRST 200 ROWS ONLY''', sid=sid, id=identifier)
    by_type = {e['event_type']:e for e in outbox}
    receipt = by_type.get('booking.received', {})
    decision = by_type.get('booking.status_changed', {})
    decision_origin = 'admin' if any(e['component']=='admin' for e in events) else 'legacy' if decision else 'pending'
    return {'booking':booking,'outbox':outbox,'effects':effects,'events':list(reversed(events)),'decision_origin':decision_origin,
            'timings':{'receipt_smtp_seconds':seconds(booking['created_at'],receipt.get('smtp_submitted_at')),
                       'human_wait_seconds':None if decision_origin=='legacy' else seconds(booking['created_at'],decision.get('created_at') or datetime.now(timezone.utc)),
                       'decision_smtp_seconds':seconds(decision.get('created_at'),decision.get('smtp_submitted_at'))}}


def kafka_messages(db, sid):
    """Bounded public reconstruction from publisher ACKs and durable effects.

    Never consume Kafka, move group offsets, or expose raw payloads/DLQ bodies.
    JSON fields are explicitly selected so future contract additions stay private.
    """
    records = rows(db, '''SELECT e.event_id,e.event_type,e.topic,e.created_at,e.published_at,
        e.partition_id,e.source_offset,e.correlation_id,
        JSON_VALUE(e.payload,'$.schema_version' RETURNING NUMBER) AS schema_version,
        JSON_VALUE(e.payload,'$.public_alias') AS public_alias,
        JSON_VALUE(e.payload,'$.restaurant_id') AS restaurant_id,
        JSON_VALUE(e.payload,'$.state') AS event_state,
        JSON_VALUE(e.payload,'$.state_version' RETURNING NUMBER) AS state_version,
        JSON_VALUE(e.payload,'$.occurred_at') AS occurred_at,
        c.processed_at AS confirmation_at,t.processed_at AS trending_at,
        n.notification_state,n.attempt_count,n.updated_at AS notification_at
        FROM LT_OWNER.outbox_events e
        LEFT JOIN LT_OWNER.processed_events c ON c.event_id=e.event_id
            AND c.session_id=e.session_id AND c.processor_name='confirmation' AND c.projection_id='live'
        LEFT JOIN LT_OWNER.processed_events t ON t.event_id=e.event_id
            AND t.session_id=e.session_id AND t.processor_name='trending' AND t.projection_id='live'
        LEFT JOIN LT_OWNER.notification_delivery n ON n.event_id=e.event_id AND n.channel='email'
        WHERE e.session_id=:sid AND e.published_at IS NOT NULL
            AND e.topic IN (:requests,:notifications)
        ORDER BY e.published_at DESC,e.event_id FETCH FIRST 100 ROWS ONLY''',
        sid=sid, requests=CREATED, notifications=CHANGED)
    messages = []
    for r in records:
        payload = {'schema_version':r['schema_version'],'event_id':r['event_id'],
                   'event_type':r['event_type'],'public_alias':r['public_alias'],
                   'restaurant_id':r['restaurant_id'],'correlation_id':r['correlation_id'],
                   'state':r['event_state'],'state_version':r['state_version'],'occurred_at':r['occurred_at']}
        effects = ({role:{'state':'RECORDED' if r[role+'_at'] else 'UNOBSERVED','at':r[role+'_at']}
                    for role in ('confirmation','trending')} if r['topic']==CREATED else
                   {'notifications':{'state':r['notification_state'] or 'UNOBSERVED','at':r['notification_at']}})
        messages.append({'event_id':r['event_id'],'topic':r['topic'],'partition_id':r['partition_id'],
                         'offset':r['source_offset'],'published_at':r['published_at'],
                         'content':payload,'effects':effects})
    return {'snapshot_at':datetime.now(timezone.utc),'session_id':sid,'limit':100,
            'source':'Publisher ACK + database effect ledger; public projection, not a Kafka read',
            'messages':messages}
