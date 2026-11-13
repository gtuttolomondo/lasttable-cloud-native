"""Migration 3: bounded technical telemetry and schema quota inside existing storage.

Never deletes bookings, outbox events, delivery records or business event logs.
"""
import argparse
import hashlib
import json
from pathlib import Path
from verify_data import CONFIG, secret, connect, scalar

PURGE_SQL = """CREATE OR REPLACE PROCEDURE purge_telemetry AUTHID DEFINER AS
BEGIN
    DELETE FROM demo_events
    WHERE action IN ('kafka.snapshot','kafka.topic.snapshot','kafka.inventory.snapshot')
      AND occurred_at < SYSTIMESTAMP - INTERVAL '1' DAY
      AND log_id NOT IN (
          SELECT MAX(log_id) FROM demo_events
          WHERE action IN ('kafka.snapshot','kafka.topic.snapshot','kafka.inventory.snapshot')
          GROUP BY session_id,action,topic);
END;"""


def report(admin, owner):
    with admin.cursor() as cursor:
        cursor.execute("SELECT bytes,max_bytes FROM dba_ts_quotas WHERE username='LT_OWNER' AND tablespace_name='DATA'")
        quota=cursor.fetchone()
    with owner.cursor() as cursor:
        cursor.execute('SELECT segment_name,segment_type,bytes FROM user_segments ORDER BY bytes DESC FETCH FIRST 8 ROWS ONLY')
        segments=[{'name':name,'type':kind,'bytes':size} for name,kind,size in cursor]
    return {'allocated_bytes':quota[0] if quota else None,'quota_bytes':quota[1] if quota else None,'largest_segments':segments,
            'technical_snapshots':scalar(owner,"SELECT COUNT(*) FROM demo_events WHERE action IN ('kafka.snapshot','kafka.topic.snapshot','kafka.inventory.snapshot')"),
            'bookings':scalar(owner,'SELECT COUNT(*) FROM bookings')}


def migrate(check=False):
    ids=CONFIG['credential_secret_ids']
    with connect('ADMIN',secret(ids['database-admin'])) as admin,connect('LT_OWNER',secret(ids['database-owner'])) as owner:
        before=report(admin,owner)
        print(json.dumps({'before':before}),flush=True)
        if check:return
        checksum=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        existing=scalar(owner,'SELECT checksum FROM schema_migrations WHERE version=3')
        if existing:
            assert existing==checksum,'Migration 3 changed after execution'
        else:
            with admin.cursor() as cursor:
                # This is a schema limit, not a resize of the existing 20 GB database.
                cursor.execute('ALTER USER LT_OWNER QUOTA 1G ON DATA')
                cursor.execute('GRANT CREATE PROCEDURE TO LT_OWNER')
            with owner.cursor() as cursor:
                cursor.execute(PURGE_SQL)
                cursor.execute('GRANT EXECUTE ON purge_telemetry TO LT_WORKER')
                cursor.execute('INSERT INTO schema_migrations(version,checksum) VALUES(3,:checksum)',checksum=checksum)
            owner.commit()
        owner.call_timeout=120000
        with owner.cursor() as cursor:cursor.callproc('purge_telemetry')
        owner.commit()
        after=report(admin,owner)
        print(json.dumps({'migration':3,'after':after,'business_data_deleted':False}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');migrate(p.parse_args().check)
