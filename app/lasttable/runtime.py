"""Shared Oracle/OCI configuration. Configuration contains references, never secrets."""
import base64
import json
import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import oci
import oracledb

oracledb.defaults.fetch_lobs = False


@lru_cache
def config():
    if os.getenv('LASTTABLE_CONFIG_JSON'):
        return json.loads(os.environ['LASTTABLE_CONFIG_JSON'])
    return json.loads(Path(os.environ.get('LASTTABLE_CONFIG', '/config/config.json')).read_text())


@lru_cache
def signer():
    if os.getenv('OCI_RESOURCE_PRINCIPAL_VERSION'):
        return oci.auth.signers.get_resource_principals_signer()
    return oci.auth.signers.get_oke_workload_identity_resource_principal_signer()


@lru_cache
def secret(name):
    cfg = config()
    # Local development mounts a private file; it is excluded from build contexts.
    if os.getenv('LASTTABLE_LOCAL_SECRETS'):
        values = json.loads(Path(os.environ['LASTTABLE_LOCAL_SECRETS']).read_text())
        return values[name]
    identifier = cfg['credential_secret_ids'][name]
    client = oci.secrets.SecretsClient({'region': cfg['region']}, signer=signer(), timeout=(5, 15))
    bundle = client.get_secret_bundle(identifier, stage='CURRENT').data
    return base64.b64decode(bundle.secret_bundle_content.content).decode()


@lru_cache
def pool(role):
    assert role in ('api', 'worker', 'notify')
    params = oracledb.PoolParams()
    params.parse_connect_string(config()['database_dsn'])
    params.set(min=0, max=4, increment=1, retry_count=0, tcp_connect_timeout=8,
               getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=10000)
    return oracledb.create_pool(user='LT_'+role.upper(), password=secret('database-'+role), params=params)


@contextmanager
def database(role='worker'):
    with pool(role).acquire() as connection:
        connection.call_timeout = 15000
        try:
            yield connection
        finally:
            connection.rollback()


def rows(connection, sql, **binds):
    with connection.cursor() as cursor:
        cursor.execute(sql, binds)
        names = [column[0].lower() for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor]


def one(connection, sql, **binds):
    result = rows(connection, sql, **binds)
    return result[0] if result else None


def execute(connection, sql, **binds):
    with connection.cursor() as cursor:
        cursor.execute(sql, binds)
        return cursor.rowcount


def current_session(connection):
    return one(connection, 'SELECT session_id, title, accepting FROM LT_OWNER.demo_sessions ORDER BY created_at DESC FETCH FIRST 1 ROW ONLY')


def log(connection, event, component, action, outcome='OK', detail=None, source=None):
    """Only controlled fields reach the public event stream; no exception text or email."""
    source = source or {}
    execute(connection, '''INSERT INTO LT_OWNER.demo_events
        (session_id,booking_id,public_alias,correlation_id,event_id,component,action,outcome,detail,topic,group_id,partition_id,source_offset)
        VALUES(:b_session,:b_booking,:b_alias,:b_correlation,:b_event,:b_component,:b_action,:b_outcome,:b_detail,:b_topic,:b_group,:b_partition,:b_offset)''',
        b_session=event['session_id'], b_booking=event.get('booking_id'), b_alias=event.get('public_alias'),
        b_correlation=event.get('correlation_id'), b_event=event.get('event_id'), b_component=component,
        b_action=action, b_outcome=outcome, b_detail=detail, b_topic=source.get('topic'),
        b_group=source.get('group'), b_partition=source.get('partition'), b_offset=source.get('offset'))


def kafka_settings(role):
    cfg = config()
    if cfg.get('local_kafka'):
        return {'bootstrap.servers': cfg['kafka_bootstrap_servers'], 'socket.timeout.ms': 10000}
    return {'bootstrap.servers': cfg['kafka_bootstrap_servers'], 'security.protocol': 'SASL_SSL',
            'sasl.mechanism': 'SCRAM-SHA-512', 'sasl.username': 'lasttable-'+role,
            'sasl.password': secret('kafka-'+role), 'ssl.ca.location': '/etc/ssl/certs/ca-certificates.crt',
            'socket.timeout.ms': 10000}
