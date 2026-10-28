"""Private-network Kafka bootstrap and real authentication/ACL smoke tests."""
import json
import time
import uuid
from confluent_kafka import Producer,Consumer,KafkaException,KafkaError
from confluent_kafka.admin import (
    AdminClient,NewTopic,AclBinding,AclBindingFilter,ResourceType,ResourcePatternType,
    AclOperation,AclPermissionType,UserScramCredentialUpsertion,ScramCredentialInfo,ScramMechanism,
)

TOPICS={
    'lasttable.bookings.created':3,
    'lasttable.bookings.status-changed':3,
    'lasttable.bookings.dlq':1,
    'lasttable.notifications.dlq':1,
}


def initialize_kafka(config,read_secret):
    password=read_secret(config['kafka_admin_secret_id'])
    username=config.get('kafka_admin_username','superuser')
    try:
        value=json.loads(password)
    except json.JSONDecodeError:
        value=None
    if isinstance(value,dict):
        username=value.get('username',username)
        password=value.get('password') or value.get('superuser_password')
        assert password, 'Unexpected managed Kafka credential format'
    assert password and not password.startswith('Awaiting ')
    def settings(user,pwd):
        return {'bootstrap.servers':config['kafka_bootstrap_servers'],'security.protocol':'SASL_SSL',
                'sasl.mechanism':'SCRAM-SHA-512','sasl.username':user,'sasl.password':pwd,
                'ssl.ca.location':'/etc/ssl/certs/ca-certificates.crt','socket.timeout.ms':10000}
    admin=AdminClient(settings(username,password))
    deadline=time.monotonic()+120
    while True:
        try:
            metadata=admin.list_topics(timeout=8)
            break
        except KafkaException as error:
            transient=error.args[0].code() in [KafkaError._TRANSPORT,KafkaError._TIMED_OUT,KafkaError._ALL_BROKERS_DOWN]
            if not transient or time.monotonic()>=deadline:raise
            time.sleep(3)
    missing=[NewTopic(t,num_partitions=n,replication_factor=1,config={'retention.ms':'604800000'})
             for t,n in TOPICS.items() if t not in metadata.topics]
    if missing:
        for future in admin.create_topics(missing,request_timeout=20).values(): future.result(timeout=25)
    deadline=time.monotonic()+60
    while True:
        metadata=admin.list_topics(timeout=10)
        if all(t in metadata.topics and len(metadata.topics[t].partitions)==n for t,n in TOPICS.items()):break
        if time.monotonic()>=deadline:raise TimeoutError('Created Kafka topics did not become visible')
        time.sleep(2)
    assert all(len(metadata.topics[t].partitions)==n for t,n in TOPICS.items())
    roles={role:read_secret(config['credential_secret_ids']['kafka-'+role])
           for role in ['producer','confirmation','trending','notifications','observer']}
    updates=[UserScramCredentialUpsertion('lasttable-'+role,
        ScramCredentialInfo(ScramMechanism.SCRAM_SHA_512,4096),pwd.encode()) for role,pwd in roles.items()]
    for future in admin.alter_user_scram_credentials(updates).values(): future.result(timeout=25)
    bindings=[]
    def allow(role,restype,name,operation,pattern=ResourcePatternType.LITERAL):
        bindings.append(AclBinding(restype,name,pattern,'User:lasttable-'+role,'*',operation,AclPermissionType.ALLOW))
    for topic in ['lasttable.bookings.created','lasttable.bookings.status-changed']:
        allow('producer',ResourceType.TOPIC,topic,AclOperation.WRITE)
        allow('producer',ResourceType.TOPIC,topic,AclOperation.DESCRIBE)
    for role in ['producer','confirmation','trending','notifications']:
        # librdkafka calls Kafka's CLUSTER resource type BROKER (numeric value 4).
        allow(role,ResourceType.BROKER,'kafka-cluster',AclOperation.IDEMPOTENT_WRITE)
    for role in ['confirmation','trending']:
        allow(role,ResourceType.TOPIC,'lasttable.bookings.created',AclOperation.READ)
        allow(role,ResourceType.TOPIC,'lasttable.bookings.created',AclOperation.DESCRIBE)
        allow(role,ResourceType.TOPIC,'lasttable.bookings.dlq',AclOperation.WRITE)
    allow('confirmation',ResourceType.GROUP,'lasttable-confirmation',AclOperation.READ)
    allow('trending',ResourceType.GROUP,'lasttable-trending',AclOperation.READ,ResourcePatternType.PREFIXED)
    allow('notifications',ResourceType.TOPIC,'lasttable.bookings.status-changed',AclOperation.READ)
    allow('notifications',ResourceType.TOPIC,'lasttable.bookings.status-changed',AclOperation.DESCRIBE)
    allow('notifications',ResourceType.TOPIC,'lasttable.notifications.dlq',AclOperation.WRITE)
    allow('notifications',ResourceType.GROUP,'lasttable-notifications',AclOperation.READ)
    for topic in TOPICS:
        allow('observer',ResourceType.TOPIC,topic,AclOperation.DESCRIBE)
    for group in ['lasttable-confirmation','lasttable-trending','lasttable-notifications']:
        allow('observer',ResourceType.GROUP,group,AclOperation.DESCRIBE)
    allow('observer',ResourceType.GROUP,'lasttable-trending-replay-',AclOperation.DESCRIBE,ResourcePatternType.PREFIXED)
    for future in admin.create_acls(bindings,request_timeout=20).values(): future.result(timeout=25)
    for role,pwd in roles.items():
        client=AdminClient(settings('lasttable-'+role,pwd))
        assert client.list_topics('lasttable.bookings.created' if role!='notifications' else 'lasttable.bookings.status-changed',timeout=15).brokers
    smoke='lasttable.bootstrap.'+uuid.uuid4().hex[:12]
    group='lasttable-bootstrap-'+uuid.uuid4().hex[:12]
    for future in admin.create_topics([NewTopic(smoke,num_partitions=1,replication_factor=1)],request_timeout=20).values():future.result(timeout=25)
    temporary=[
        AclBinding(ResourceType.TOPIC,smoke,ResourcePatternType.LITERAL,'User:lasttable-producer','*',AclOperation.WRITE,AclPermissionType.ALLOW),
        AclBinding(ResourceType.TOPIC,smoke,ResourcePatternType.LITERAL,'User:lasttable-producer','*',AclOperation.DESCRIBE,AclPermissionType.ALLOW),
        AclBinding(ResourceType.TOPIC,smoke,ResourcePatternType.LITERAL,'User:lasttable-confirmation','*',AclOperation.READ,AclPermissionType.ALLOW),
        AclBinding(ResourceType.GROUP,group,ResourcePatternType.LITERAL,'User:lasttable-confirmation','*',AclOperation.READ,AclPermissionType.ALLOW),
    ]
    for f in admin.create_acls(temporary,request_timeout=20).values():f.result(timeout=25)
    expected=uuid.uuid4().hex.encode();delivery=[]
    producer=Producer({**settings('lasttable-producer',roles['producer']),'enable.idempotence':True,'message.timeout.ms':15000})
    producer.produce(smoke,key=b'bootstrap',value=expected,on_delivery=lambda err,msg:delivery.append(err))
    assert producer.flush(20)==0 and delivery==[None]
    consumer=Consumer({**settings('lasttable-confirmation',roles['confirmation']),
        'group.id':group,'auto.offset.reset':'earliest','enable.auto.commit':False})
    try:
        consumer.subscribe([smoke]);message=None;deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            item=consumer.poll(1)
            if item is None:continue
            if item.error():raise KafkaException(item.error())
            message=item;break
        assert message and message.value()==expected and message.key()==b'bootstrap'
        consumer.commit(message=message,asynchronous=False)
    finally: consumer.close()
    denied=[]
    observer=Producer({**settings('lasttable-observer',roles['observer']),'message.timeout.ms':10000})
    observer.produce('lasttable.bookings.created',value=b'denied-probe',on_delivery=lambda err,msg:denied.append(err))
    observer.flush(15)
    assert denied and denied[0] and denied[0].code()==KafkaError.TOPIC_AUTHORIZATION_FAILED
    for restype,name in [(ResourceType.TOPIC,smoke),(ResourceType.GROUP,group)]:
        filt=AclBindingFilter(restype,name,ResourcePatternType.LITERAL,None,None,AclOperation.ANY,AclPermissionType.ANY)
        for f in admin.delete_acls([filt],request_timeout=20).values(): f.result(timeout=25)
    for f in admin.delete_topics([smoke],request_timeout=20).values():f.result(timeout=25)
    for f in admin.delete_consumer_groups([group],request_timeout=20).values():f.result(timeout=25)
    print(json.dumps({'kafka':'passed','topics':TOPICS,'scram_users':len(roles),'acls':len(bindings),
        'authenticated_roundtrip':'passed','observer_write_denied':'passed','temporary_topic_and_group':'removed'}),flush=True)
