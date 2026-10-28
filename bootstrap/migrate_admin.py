"""Migration 2: manual booking decisions and editable restaurant catalog.

Run with the existing bootstrap workload identity and configuration. Never
changes migration 1, recreates tables, or removes existing bookings.
"""
import hashlib
from pathlib import Path
from verify_data import CONFIG, secret, connect, scalar


def migrate():
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    password = secret(CONFIG['credential_secret_ids']['database-owner'])
    with connect('LT_OWNER', password) as db:
        existing = scalar(db, 'SELECT checksum FROM schema_migrations WHERE version=2')
        if existing:
            assert existing == checksum, 'Migration 2 changed after execution'
            print('Migration 2 already applied')
            return
        with db.cursor() as cursor:
            # Add the wider constraint first; a retry after interrupted DDL is safe.
            if not scalar(db, "SELECT COUNT(*) FROM user_constraints WHERE constraint_name='BOOKINGS_STATE_V2'"):
                cursor.execute("ALTER TABLE bookings ADD CONSTRAINT bookings_state_v2 CHECK (booking_state IN ('PENDING','CONFIRMED','REJECTED'))")
            cursor.execute("SELECT constraint_name,search_condition_vc FROM user_constraints WHERE table_name='BOOKINGS' AND constraint_type='C'")
            old = [(name, condition) for name, condition in cursor.fetchall()
                   if name != 'BOOKINGS_STATE_V2' and 'BOOKING_STATE' in (condition or '').upper()
                   and 'CONFIRMED' in (condition or '').upper()]
            for name, _ in old:
                assert name.replace('_', '').isalnum()
                cursor.execute('ALTER TABLE bookings DROP CONSTRAINT "'+name+'"')
            cursor.execute('GRANT UPDATE (booking_state,state_version,confirmed_at) ON bookings TO LT_API')
            cursor.execute('GRANT INSERT, UPDATE ON restaurants TO LT_API')
            for table, columns in [('restaurants', [('name',100),('cuisine',80),('area',80),('description',600),('price_label',20)]),
                                   ('bookings', [('recipient_email',320)])]:
                for column, size in columns:
                    cursor.execute(f'ALTER TABLE {table} MODIFY ({column} VARCHAR2({size} CHAR))')
            cursor.execute('INSERT INTO schema_migrations(version,checksum) VALUES(2,:checksum)', checksum=checksum)
        db.commit()
    print('Migration 2 applied: manual decisions, API column grants, restaurant editing')


if __name__ == '__main__':
    migrate()
