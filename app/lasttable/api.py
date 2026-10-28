import hmac
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal

import oracledb
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .events import insert_outbox, new_event
from .observability import dashboard_metrics, booking_trace, kafka_messages
from .runtime import config, current_session, database, execute, log, one, rows, secret

app = FastAPI(title='LastTable', docs_url=None, redoc_url=None, openapi_url=None)
RATE = defaultdict(deque)
AUTH_RATE = defaultdict(deque)


@app.middleware('http')
async def private_admin_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/admin/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class Reservation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    booking_id: uuid.UUID
    restaurant_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,31}$')
    nickname: str = Field(min_length=1, max_length=60)
    email: EmailStr
    party_size: int = Field(ge=1, le=12)
    reservation_time: str = Field(pattern=r'^(19|20|21|22):(00|30)$')


def admin(authorization: str = Header(default='')):
    expected = 'Bearer '+secret('demo-admin')
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, 'Accesso relatore richiesto')


def table_admin(request: Request, authorization: str = Header(default='')):
    code = os.getenv('LASTTABLE_TABLE_ADMIN_CODE', '')
    if not code:
        raise HTTPException(503, 'Accesso amministrativo non configurato')
    now = time.monotonic()
    bucket = AUTH_RATE[request.client.host if request.client else 'unknown']
    while bucket and bucket[0] < now-60:
        bucket.popleft()
    if len(bucket) >= 10:
        raise HTTPException(429, 'Troppi tentativi. Riprova tra un minuto')
    if not hmac.compare_digest(authorization, 'Bearer '+code):
        bucket.append(now)
        raise HTTPException(401, 'Codice di accesso non valido')


def rate_limit(request):
    now = time.monotonic()
    key = request.client.host if request.client else 'unknown'
    bucket = RATE[key]
    while bucket and bucket[0] < now-60:
        bucket.popleft()
    # A webinar audience can share one public IP behind NAT or a load balancer.
    if len(bucket) >= 120:
        raise HTTPException(429, 'Attendi un minuto prima di riprovare')
    bucket.append(now)
    if len(RATE) > 10000:
        for old in list(RATE):
            if not RATE[old] or RATE[old][-1] < now-60:
                RATE.pop(old, None)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'release': os.getenv('LASTTABLE_RELEASE', 'local')}


@app.get('/api/ready')
def ready():
    with database('api') as db:
        assert one(db, 'SELECT 1 AS healthy FROM dual')['healthy'] == 1
    return {'status': 'ready'}


@app.get('/api/restaurants')
def restaurants():
    with database('api') as db:
        return {'session': current_session(db), 'restaurants': rows(db, 'SELECT * FROM LT_OWNER.restaurants ORDER BY restaurant_id')}


def booking_status(db, booking_id):
    booking = one(db, '''SELECT booking_id,public_alias,restaurant_id,party_size,reservation_time,
        booking_state,created_at,confirmed_at FROM LT_OWNER.bookings WHERE booking_id=:b_id''', b_id=str(booking_id))
    if not booking:
        raise HTTPException(404, 'Prenotazione non trovata')
    def delivery(event_type):
        return one(db, """SELECT COALESCE(n.notification_state,'PENDING') AS notification_state,
            COALESCE(n.attempt_count,0) AS attempt_count,n.error_code,n.smtp_submitted_at
            FROM LT_OWNER.outbox_events e LEFT JOIN LT_OWNER.notification_delivery n ON n.event_id=e.event_id
            WHERE e.aggregate_id=:b_id AND e.event_type=:b_type
            ORDER BY e.created_at DESC FETCH FIRST 1 ROW ONLY""", b_id=str(booking_id), b_type=event_type)
    booking['acknowledgement'] = delivery('booking.received') or {'notification_state':'NOT_REQUESTED','attempt_count':0}
    booking['notification'] = delivery('booking.status_changed') or {'notification_state':'PENDING','attempt_count':0}
    return booking


@app.post('/api/bookings', status_code=202)
def reserve(body: Reservation, request: Request):
    rate_limit(request)
    booking_id = str(body.booking_id)
    with database('api') as db:
        session = current_session(db)
        if not session or not session['accepting']:
            raise HTTPException(409, 'La sessione demo non accetta prenotazioni')
        existing = one(db, 'SELECT booking_id FROM LT_OWNER.bookings WHERE booking_id=:b_id', b_id=booking_id)
        if existing:
            return booking_status(db, booking_id)
        if not one(db, 'SELECT restaurant_id FROM LT_OWNER.restaurants WHERE restaurant_id=:b_id', b_id=body.restaurant_id):
            raise HTTPException(404, 'Ristorante non trovato')
        count = one(db, 'SELECT COUNT(*) AS total FROM LT_OWNER.bookings WHERE session_id=:b_session', b_session=session['session_id'])['total']
        if count >= 1000:
            raise HTTPException(429, 'Capienza della sessione demo raggiunta')
        booking = {'booking_id': booking_id, 'public_alias': 'LT-'+uuid.uuid4().hex[:12].upper(),
                   'session_id': session['session_id'], 'restaurant_id': body.restaurant_id}
        try:
            execute(db, '''INSERT INTO LT_OWNER.bookings
                (booking_id,public_alias,session_id,restaurant_id,nickname,recipient_email,party_size,reservation_time)
                VALUES(:b_id,:b_alias,:b_session,:b_restaurant,:b_nickname,:b_email,:b_party,:b_time)''',
                b_id=booking_id, b_alias=booking['public_alias'], b_session=booking['session_id'],
                b_restaurant=body.restaurant_id, b_nickname=body.nickname.strip(), b_email=str(body.email),
                b_party=body.party_size, b_time=body.reservation_time)
            event = new_event(booking, 'booking.created')
            insert_outbox(db, event)
            # Receipt email is queued atomically with the booking. It never approves it.
            insert_outbox(db, new_event(booking, 'booking.received'))
            log(db, event, 'api', 'booking.accepted', detail='Prenotazione e outbox salvati nella stessa transazione')
            db.commit()
        except oracledb.IntegrityError as error:
            db.rollback()
            if error.args[0].code != 1:
                raise
            # A concurrent retry with the same opaque request ID returns its original result.
        return booking_status(db, booking_id)


@app.get('/api/bookings/{booking_id}')
def status(booking_id: uuid.UUID):
    with database('api') as db:
        return booking_status(db, booking_id)


@app.get('/api/demo')
def dashboard():
    with database('api') as db:
        execute(db, 'SET TRANSACTION READ ONLY')
        session = current_session(db)
        sid = session['session_id']
        bookings = rows(db, 'SELECT booking_state AS state,COUNT(*) AS total FROM LT_OWNER.bookings WHERE session_id=:b_session GROUP BY booking_state', b_session=sid)
        notifications = rows(db, '''SELECT n.notification_state AS state,COUNT(*) AS total FROM LT_OWNER.notification_delivery n
            JOIN LT_OWNER.bookings b ON b.booking_id=n.booking_id WHERE b.session_id=:b_session GROUP BY n.notification_state''', b_session=sid)
        pending = one(db, 'SELECT COUNT(*) AS total FROM LT_OWNER.outbox_events WHERE session_id=:b_session AND published_at IS NULL', b_session=sid)['total']
        events = rows(db, '''SELECT log_id,public_alias,correlation_id,event_id,component,action,outcome,detail,topic,group_id,partition_id,source_offset,occurred_at
            FROM LT_OWNER.demo_events WHERE session_id=:b_session AND action NOT IN ('kafka.snapshot','kafka.topic.snapshot','kafka.inventory.snapshot') ORDER BY log_id DESC FETCH FIRST 100 ROWS ONLY''', b_session=sid)
        ranking = rows(db, '''SELECT r.restaurant_id,r.name,COALESCE(s.booking_count,0) AS booking_count FROM LT_OWNER.restaurants r
            LEFT JOIN LT_OWNER.restaurant_stats s ON r.restaurant_id=s.restaurant_id AND s.session_id=:b_session AND s.projection_id='live'
            ORDER BY booking_count DESC,r.name''', b_session=sid)
        workers = rows(db, 'SELECT component,worker_state,last_seen_at FROM LT_OWNER.worker_status ORDER BY component')
        controls = one(db, 'SELECT notifications_pause_requested,updated_at FROM LT_OWNER.demo_controls WHERE session_id=:b_session', b_session=sid)
        replays = rows(db, '''SELECT run_id,projection_id,run_state,records_processed,requested_at,completed_at,error_code,expected_json,actual_json
            FROM LT_OWNER.replay_runs WHERE session_id=:b_session ORDER BY requested_at DESC FETCH FIRST 5 ROWS ONLY''', b_session=sid)
        metrics = one(db, '''SELECT detail,occurred_at FROM LT_OWNER.demo_events
            WHERE session_id=:b_session AND action='kafka.snapshot' ORDER BY log_id DESC FETCH FIRST 1 ROW ONLY''', b_session=sid)
        return {'snapshot_at': datetime.now(timezone.utc), 'source': 'Oracle Database + Kafka Admin API',
            'session': session, 'bookings': bookings, 'notifications': notifications, 'outbox_pending': pending,
            'ranking': ranking, 'events': events, 'workers': workers, 'controls': controls, 'replays': replays,
            'kafka': json.loads(metrics['detail']) if metrics else None,
            'kafka_snapshot_at': metrics['occurred_at'] if metrics else None,
            'telemetry': dashboard_metrics(db, sid)}


@app.get('/api/demo/messages')
def public_kafka_messages():
    with database('api') as db:
        execute(db, 'SET TRANSACTION READ ONLY')
        return kafka_messages(db, current_session(db)['session_id'])


@app.get('/api/demo/bookings/{public_alias}')
def public_booking_trace(public_alias: str):
    if not re.fullmatch(r'LT-[A-F0-9]{12}', public_alias):
        raise HTTPException(404, 'Prenotazione non trovata')
    with database('api') as db:
        execute(db, 'SET TRANSACTION READ ONLY')
        result = booking_trace(db, current_session(db)['session_id'], public_alias)
        if not result:
            raise HTTPException(404, 'Prenotazione non trovata nella sessione corrente')
        return result


class PauseRequest(BaseModel):
    paused: bool


class BookingDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: Literal['CONFIRMED', 'REJECTED']
    booking_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    all_pending: bool = False


class RestaurantDetails(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    cuisine: str = Field(min_length=1, max_length=80)
    area: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=600)
    icon: Literal['sun', 'waves', 'leaf'] = 'sun'
    price_label: str = Field(min_length=1, max_length=20)


class NewRestaurant(RestaurantDetails):
    restaurant_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,31}$')


@app.get('/api/admin/bookings', dependencies=[Depends(table_admin)])
def admin_bookings():
    with database('api') as db:
        execute(db, 'SET TRANSACTION READ ONLY')
        session = current_session(db)
        bookings = rows(db, '''SELECT b.booking_id,b.public_alias,b.restaurant_id,r.name AS restaurant_name,
            b.nickname,b.recipient_email,b.party_size,b.reservation_time,b.booking_state,b.created_at,b.confirmed_at
            FROM LT_OWNER.bookings b JOIN LT_OWNER.restaurants r ON r.restaurant_id=b.restaurant_id
            WHERE b.session_id=:b_session ORDER BY b.created_at DESC''', b_session=session['session_id'])
        return {'session': session, 'bookings': bookings}


@app.post('/api/admin/bookings/decision', dependencies=[Depends(table_admin)])
def decide_bookings(body: BookingDecision):
    if body.all_pending == bool(body.booking_ids):
        raise HTTPException(422, 'Scegli le prenotazioni oppure tutte quelle in attesa')
    with database('api') as db:
        sid = current_session(db)['session_id']
        # Deterministic row locking prevents conflicting concurrent bulk decisions.
        if body.all_pending:
            candidates = rows(db, "SELECT booking_id FROM LT_OWNER.bookings WHERE session_id=:b_session AND booking_state='PENDING' ORDER BY booking_id", b_session=sid)
            identifiers = [b['booking_id'] for b in candidates]
        else:
            identifiers = sorted({str(identifier) for identifier in body.booking_ids})
        updated, skipped = [], []
        for identifier in identifiers:
            booking = one(db, '''SELECT booking_id,public_alias,session_id,restaurant_id,booking_state
                FROM LT_OWNER.bookings WHERE booking_id=:b_id AND session_id=:b_session FOR UPDATE''',
                b_id=identifier, b_session=sid)
            if not booking:
                raise HTTPException(404, 'Prenotazione non trovata nella sessione corrente')
            if booking['booking_state'] != 'PENDING':
                skipped.append(booking['public_alias'])
                continue
            execute(db, '''UPDATE LT_OWNER.bookings SET booking_state=:b_state,state_version=1,
                confirmed_at=CASE WHEN :b_state='CONFIRMED' THEN SYSTIMESTAMP ELSE NULL END WHERE booking_id=:b_id''',
                b_state=body.state, b_id=identifier)
            event = new_event({**booking, 'state': body.state}, 'booking.status_changed')
            insert_outbox(db, event)
            log(db, event, 'admin', 'booking.'+('confirmed' if body.state=='CONFIRMED' else 'rejected'),
                detail='Decisione amministratore e notifica outbox salvate nella stessa transazione')
            updated.append(booking['public_alias'])
        db.commit()
        return {'state': body.state, 'updated': updated, 'skipped': skipped}


@app.post('/api/admin/restaurants', status_code=201, dependencies=[Depends(table_admin)])
def create_restaurant(body: NewRestaurant):
    with database('api') as db:
        try:
            execute(db, '''INSERT INTO LT_OWNER.restaurants(restaurant_id,name,cuisine,area,description,icon,price_label)
                VALUES(:restaurant_id,:name,:cuisine,:area,:description,:icon,:price_label)''', **body.model_dump())
            db.commit()
        except oracledb.IntegrityError as error:
            if error.args[0].code != 1:
                raise
            raise HTTPException(409, 'Identificativo ristorante già utilizzato')
        return body.model_dump()


@app.put('/api/admin/restaurants/{restaurant_id}', dependencies=[Depends(table_admin)])
def update_restaurant(restaurant_id: str, body: RestaurantDetails):
    with database('api') as db:
        changed = execute(db, '''UPDATE LT_OWNER.restaurants SET name=:name,cuisine=:cuisine,area=:area,
            description=:description,icon=:icon,price_label=:price_label WHERE restaurant_id=:restaurant_id''',
            restaurant_id=restaurant_id, **body.model_dump())
        if not changed:
            raise HTTPException(404, 'Ristorante non trovato')
        db.commit()
        return {'restaurant_id': restaurant_id, **body.model_dump()}


@app.post('/api/admin/notifications', dependencies=[Depends(admin)])
def pause(body: PauseRequest):
    with database('api') as db:
        sid = current_session(db)['session_id']
        execute(db, 'UPDATE LT_OWNER.demo_controls SET notifications_pause_requested=:b_pause,updated_at=SYSTIMESTAMP WHERE session_id=:b_session', b_pause=int(body.paused), b_session=sid)
        db.commit()
    return {'pause_requested': body.paused}


@app.post('/api/admin/replay', status_code=202, dependencies=[Depends(admin)])
def replay():
    with database('api') as db:
        sid = current_session(db)['session_id']
        if one(db, "SELECT run_id FROM LT_OWNER.replay_runs WHERE run_state IN ('REQUESTED','RUNNING')"):
            raise HTTPException(409, 'Un replay è già in corso')
        rid = str(uuid.uuid4())
        execute(db, '''INSERT INTO LT_OWNER.replay_runs(run_id,session_id,projection_id,group_id,run_state)
            VALUES(:b_id,:b_session,:b_projection,:b_group,'REQUESTED')''', b_id=rid, b_session=sid,
            b_projection='replay-'+rid, b_group='lasttable-trending-replay-'+rid)
        db.commit()
        return {'run_id': rid}


@app.post('/api/admin/session', status_code=201, dependencies=[Depends(admin)])
def start_session():
    sid = str(uuid.uuid4())
    with database('api') as db:
        execute(db, 'UPDATE LT_OWNER.demo_sessions SET accepting=0 WHERE accepting=1')
        execute(db, 'INSERT INTO LT_OWNER.demo_sessions(session_id,title) VALUES(:b_id,:b_title)',
            b_id=sid, b_title='LastTable · From Code to Cloud')
        execute(db, 'INSERT INTO LT_OWNER.demo_controls(session_id) VALUES(:b_id)', b_id=sid)
        db.commit()
    return {'session_id': sid}
