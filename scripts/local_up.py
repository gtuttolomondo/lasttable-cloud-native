"""Start application processes in Podman; local Kafka/Mailpit and private ADB tunnel required."""
import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(*args):
    values=json.loads((ROOT/'.local/local-secrets.json').read_text())
    env={**os.environ,'LASTTABLE_TABLE_ADMIN_CODE':values['table-admin']}
    return subprocess.run(['podman',*args],env=env,check=True,capture_output=True,text=True).stdout.strip()


def base():
    cfg=json.loads((ROOT/'.local/local-config.json').read_text())
    import re
    host=re.search(r'\(host=([^)]*)\)',cfg['database_dsn']).group(1)
    ip=run('run','--rm','--platform','linux/amd64','docker.io/library/alpine:3.22','getent','hosts','host.containers.internal').split()[0]
    return ['--platform','linux/amd64','--network','lasttable-local','--add-host',host+':'+ip,
        '-v',str(ROOT/'.local/local-config.json')+':/config/config.json:ro',
        '-v',str(ROOT/'.local/local-secrets.json')+':/run/local-secrets.json:ro',
        '-v',str(ROOT/'app/lasttable')+':/app/lasttable:ro',
        '-e','LASTTABLE_TABLE_ADMIN_CODE','-e','LASTTABLE_LOCAL_SECRETS=/run/local-secrets.json','-e','PYTHONPATH=/app']


def up():
    common=base()
    script="""from confluent_kafka.admin import AdminClient,NewTopic
import time
a=AdminClient({'bootstrap.servers':'lasttable-kafka:9092'})
topics={'lasttable.bookings.created':3,'lasttable.bookings.status-changed':3,'lasttable.bookings.dlq':1,'lasttable.notifications.dlq':1}
m=a.list_topics(timeout=15)
for f in a.create_topics([NewTopic(t,num_partitions=n,replication_factor=1) for t,n in topics.items() if t not in m.topics]).values():f.result(20)
print('Local Kafka topics ready')"""
    print(run('run','--rm',*common,'lasttable-backend:dev','python','-c',script))
    services={'api':[], 'notifications-function':['python','-m','lasttable.local_function']}
    services.update({role:['python','-m','lasttable.worker',role] for role in ['publisher','confirmation','trending','notifications','observer','replay']})
    for name,command in services.items():
        cname='lasttable-'+('notifications' if name=='notifications-function' else name+'-worker' if name=='notifications' else name)
        exists=subprocess.run(['podman','container','exists',cname]).returncode==0
        if exists:continue
        ports=['-p','127.0.0.1:18000:8000'] if name=='api' else []
        run('run','-d','--name',cname,*common,*ports,'lasttable-backend:dev',*command)
        print('Started '+cname)


if __name__=='__main__':
    up()
