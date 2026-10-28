"""Initialize and verify the demo data services from a private OKE workload.

No user API keys or plaintext passwords are baked into this image or logged.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
import oci
import oracledb

oracledb.defaults.fetch_lobs = False
CONFIG = json.loads(Path(os.environ.get('BOOTSTRAP_CONFIG_PATH','/config/config.json')).read_text())
SIGNER = oci.auth.signers.get_oke_workload_identity_resource_principal_signer()
SECRETS = oci.secrets.SecretsClient({'region':CONFIG['region']},signer=SIGNER,timeout=(5,20))


def secret(identifier):
    bundle = SECRETS.get_secret_bundle(identifier, stage='CURRENT').data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


def connect(user,password):
    params=oracledb.ConnectParams()
    params.parse_connect_string(CONFIG['database_dsn'])
    params.set(retry_count=0,tcp_connect_timeout=8)
    c=oracledb.connect(user=user,password=password,params=params)
    c.call_timeout=15000
    return c


def scalar(connection,sql,binds=None):
    with connection.cursor() as cursor:
        cursor.execute(sql,binds or {})
        row=cursor.fetchone()
        return row[0] if row else None


def initialize_database():
    ids=CONFIG['credential_secret_ids']
    passwords={role:secret(ids['database-'+role]) for role in ['admin','owner','api','worker','notify']}
    users={'owner':'LT_OWNER','api':'LT_API','worker':'LT_WORKER','notify':'LT_NOTIFY'}
    with connect('ADMIN',passwords['admin']) as admin:
        with admin.cursor() as cursor:
            for role,name in users.items():
                exists=scalar(admin,'SELECT COUNT(*) FROM all_users WHERE username=:name',{'name':name})
                if not exists:
                    password=passwords[role]
                    assert re.fullmatch(r'[A-Za-z0-9!#%+_=\-]{20,64}',password)
                    cursor.execute(f'CREATE USER "{name}" IDENTIFIED BY "{password}"')
                cursor.execute(f'GRANT CREATE SESSION TO "{name}"')
            cursor.execute('GRANT CREATE TABLE, CREATE VIEW, CREATE SEQUENCE TO LT_OWNER')
            cursor.execute('ALTER USER LT_OWNER QUOTA 100M ON DATA')
    ddl=Path('/bootstrap/schema.sql').read_text()
    checksum=hashlib.sha256(ddl.encode()).hexdigest()
    with connect('LT_OWNER',passwords['owner']) as owner:
        try:
            existing=scalar(owner,'SELECT checksum FROM schema_migrations WHERE version=1')
        except oracledb.DatabaseError as error:
            if error.args[0].code!=942: raise
            existing=None
        if existing:
            assert existing==checksum, 'Migration 1 changed; introduce a new versioned migration'
        else:
            with owner.cursor() as cursor:
                for statement in ddl.split(';'):
                    if not statement.strip(): continue
                    try: cursor.execute(statement.strip())
                    except oracledb.DatabaseError as error:
                        if error.args[0].code!=955: raise
                cursor.execute('INSERT INTO schema_migrations(version,checksum) VALUES(1,:checksum)',checksum=checksum)
            owner.commit()
        with owner.cursor() as cursor:
            seeds=[
                ('aurora','Aurora','Cucina contemporanea','Centro','Sapori di stagione, luci calde e una cucina da condividere.','✦','€€'),
                ('del-porto','Del Porto','Pesce e Mediterraneo','Navigli','Una serata sul mare, anche nel cuore della città.','≈','€€€'),
                ('tavola-verde','Tavola Verde','Vegetale e creativa','Brera','Ingredienti locali e nuove idee, dal primo all’ultimo assaggio.','❋','€€'),
            ]
            for row in seeds:
                cursor.execute('MERGE INTO restaurants r USING (SELECT :id restaurant_id FROM dual) s ON (r.restaurant_id=s.restaurant_id) WHEN NOT MATCHED THEN INSERT (restaurant_id,name,cuisine,area,description,icon,price_label) VALUES (:id,:name,:cuisine,:area,:description,:icon,:price)',
                    id=row[0],name=row[1],cuisine=row[2],area=row[3],description=row[4],icon=row[5],price=row[6])
            if scalar(owner,'SELECT COUNT(*) FROM demo_sessions')==0:
                session=str(uuid.uuid4())
                cursor.execute('INSERT INTO demo_sessions(session_id,title) VALUES(:id,:title)',id=session,title='LastTable · From Code to Cloud')
                cursor.execute('INSERT INTO demo_controls(session_id) VALUES(:id)',id=session)
            grants={
                'LT_API':{
                    'restaurants':'SELECT','bookings':'SELECT, INSERT','demo_sessions':'SELECT, INSERT, UPDATE',
                    'outbox_events':'SELECT, INSERT','notification_delivery':'SELECT','restaurant_stats':'SELECT',
                    'processed_events':'SELECT','demo_events':'SELECT, INSERT','demo_controls':'SELECT, INSERT, UPDATE',
                    'replay_runs':'SELECT, INSERT','worker_status':'SELECT'},
                'LT_WORKER':{
                    'restaurants':'SELECT','bookings':'SELECT, UPDATE','demo_sessions':'SELECT',
                    'outbox_events':'SELECT, INSERT, UPDATE','processed_events':'SELECT, INSERT',
                    'restaurant_stats':'SELECT, INSERT, UPDATE','demo_events':'SELECT, INSERT',
                    'demo_controls':'SELECT','worker_status':'SELECT, INSERT, UPDATE',
                    'replay_runs':'SELECT, UPDATE','notification_delivery':'SELECT, INSERT, UPDATE'},
                'LT_NOTIFY':{'booking_recipients':'SELECT','notification_delivery':'SELECT, INSERT, UPDATE','demo_events':'INSERT'},
            }
            for user,tables in grants.items():
                for table,permissions in tables.items():
                    cursor.execute(f'GRANT {permissions} ON {table} TO {user}')
        owner.commit()
    # Oracle-specific tests, including rollback and least privilege.
    with connect('LT_API',passwords['api']) as api, connect('LT_OWNER',passwords['owner']) as owner:
        assert scalar(api,'SELECT COUNT(*) FROM LT_OWNER.restaurants')>=3
        session=scalar(owner,'SELECT session_id FROM demo_sessions ORDER BY created_at DESC FETCH FIRST 1 ROW ONLY')
        booking=str(uuid.uuid4());event=str(uuid.uuid4());correlation=str(uuid.uuid4())
        payload=json.dumps({'schema_version':1,'event_id':event,'booking_id':booking,'session_id':session,'correlation_id':correlation})
        def insert():
            with api.cursor() as cursor:
                alias='PROBE-'+uuid.uuid4().hex[:12]
                cursor.execute("INSERT INTO LT_OWNER.bookings(booking_id,public_alias,session_id,restaurant_id,nickname,recipient_email,party_size,reservation_time) VALUES(:booking,:alias,:b_session,'aurora','bootstrap-probe','probe@example.invalid',2,'20:00')",booking=booking,alias=alias,b_session=session)
                cursor.execute("INSERT INTO LT_OWNER.outbox_events(event_id,aggregate_id,aggregate_version,event_type,topic,payload,session_id,correlation_id) VALUES(:event,:booking,0,'booking.created','lasttable.bookings.created',:payload,:b_session,:correlation)",event=event,booking=booking,payload=payload,b_session=session,correlation=correlation)
        insert();api.rollback()
        assert scalar(owner,'SELECT COUNT(*) FROM bookings WHERE booking_id=:id',{'id':booking})==0
        assert scalar(owner,'SELECT COUNT(*) FROM outbox_events WHERE event_id=:id',{'id':event})==0
        insert();api.commit()
        assert scalar(owner,'SELECT COUNT(*) FROM bookings WHERE booking_id=:id',{'id':booking})==1
        assert scalar(owner,'SELECT COUNT(*) FROM outbox_events WHERE event_id=:id',{'id':event})==1
        try:
            with connect('LT_WORKER',passwords['worker']) as worker:
                with worker.cursor() as cursor:
                    cursor.execute("UPDATE LT_OWNER.bookings SET booking_state='CONFIRMED',state_version=1,confirmed_at=SYSTIMESTAMP WHERE booking_id=:id",id=booking)
                    changed=str(uuid.uuid4())
                    cursor.execute("INSERT INTO LT_OWNER.outbox_events(event_id,aggregate_id,aggregate_version,event_type,topic,payload,session_id,correlation_id) VALUES(:event,:booking,1,'booking.status_changed','lasttable.bookings.status-changed',:payload,:b_session,:correlation)",event=changed,booking=booking,payload=payload,b_session=session,correlation=correlation)
                worker.commit()
            assert scalar(owner,'SELECT state_version FROM bookings WHERE booking_id=:id',{'id':booking})==1
            assert scalar(owner,'SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=:id',{'id':booking})==2
            for user,role in [('LT_NOTIFY','notify')]:
                with connect(user,passwords[role]) as restricted:
                    try:
                        with restricted.cursor() as cursor:
                            cursor.execute("UPDATE LT_OWNER.bookings SET booking_state='PENDING' WHERE 1=0")
                    except oracledb.DatabaseError as error:
                        assert error.args[0].code in [942,1031,41900]
                    else: raise AssertionError(f'{user} unexpectedly has permission to update bookings')
        finally:
            with owner.cursor() as cursor:
                cursor.execute('DELETE FROM outbox_events WHERE aggregate_id=:id',id=booking)
                cursor.execute('DELETE FROM bookings WHERE booking_id=:id',id=booking)
            owner.commit()
    print(json.dumps({'database':'passed','schema_migration':1,'restaurants':3,'atomic_rollback':'passed','confirmation_and_outbox':'passed','notification_booking_write_denied':'passed'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database-only',action='store_true');parser.add_argument('--kafka-only',action='store_true');args=parser.parse_args()
    if not args.kafka_only:
        initialize_database()
        from migrate_admin import migrate
        migrate()
    if not args.database_only:
        from verify_kafka import initialize_kafka
        initialize_kafka(CONFIG,secret)
