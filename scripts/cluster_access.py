"""Temporary, allowlisted Bastion access to the private OKE API.

Run prepare, then status until ACTIVE, then tunnel in a retained terminal.
Keys, session IDs, known hosts and kubeconfig remain under ignored .local/.
"""
import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
import yaml
import oci
import cloud

ROOT=cloud.ROOT
PRIVATE=cloud.PRIVATE
KEY=PRIVATE/'bastion-ssh'
LOCAL_PORT=int(os.environ.get('LASTTABLE_KUBE_PORT','16443'))
assert 1024 <= LOCAL_PORT <= 65535

def outputs():
    return json.loads((PRIVATE/'terraform-state.json').read_text())['outputs']

def prepare():
    data=outputs()
    ce=cloud.client(oci.container_engine.ContainerEngineClient)
    platform=data.get('platform',{}).get('value')
    if platform:
        cluster_id=platform['cluster_id']
    else:
        variables=json.loads((PRIVATE/'variables.json').read_text())
        name=variables.get('project_name','lasttable')+'-oke'
        matches=[c for c in ce.list_clusters(cloud.COMPARTMENT).data if c.name==name
            and c.vcn_id==data['network']['value']['vcn_id'] and c.lifecycle_state=='ACTIVE']
        assert len(matches)==1, 'Expected exactly one active cluster owned by the demo network'
        cluster_id=matches[0].id
    cluster=ce.get_cluster(cluster_id).data
    assert cluster.lifecycle_state=='ACTIVE'
    endpoint=cluster.endpoints.private_endpoint
    ip,port=endpoint.removeprefix('https://').split(':')
    if not KEY.exists():
        subprocess.run(['ssh-keygen','-q','-t','rsa','-b','3072','-N','','-f',str(KEY)],check=True)
    b=cloud.client(oci.bastion.BastionClient)
    old=PRIVATE/'bastion-session.json'
    session=None
    if old.exists():
        try:
            previous=b.get_session(json.loads(old.read_text())['id']).data
            if previous.lifecycle_state in ['ACTIVE','CREATING']:
                session=previous
        except oci.exceptions.ServiceError as e:
            if e.status!=404: raise
    if session is None:
        session=b.create_session(oci.bastion.models.CreateSessionDetails(
            display_name='lasttable-private-oke',bastion_id=data['security']['value']['bastion_id'],
            target_resource_details=oci.bastion.models.CreatePortForwardingSessionTargetResourceDetails(
                session_type='PORT_FORWARDING',target_resource_private_ip_address=ip,target_resource_port=int(port)),
            key_type='PUB',key_details=oci.bastion.models.PublicKeyDetails(public_key_content=(PRIVATE/'bastion-ssh.pub').read_text()),
            session_ttl_in_seconds=10800)).data
    cloud.save('bastion-session',session)
    response=ce.create_kubeconfig(cluster_id,create_cluster_kubeconfig_content_details=
        oci.container_engine.models.CreateClusterKubeconfigContentDetails(token_version='2.0.0',endpoint='PRIVATE_ENDPOINT'))
    kube=yaml.safe_load(response.data.content)
    # Keep TLS validation against the real endpoint while connecting over loopback.
    for entry in kube['clusters']:
        entry['cluster']['server']='https://127.0.0.1:'+str(LOCAL_PORT)
        entry['cluster']['tls-server-name']=ip
    wrapper=PRIVATE/'oci-exec'
    wrapper.write_text('#!/bin/sh\ncd '+shlex.quote(str(Path.home()/'.oci'))+' || exit 1\nexec /opt/homebrew/bin/oci "$@"\n')
    wrapper.chmod(0o700)
    for user in kube['users']:
        user['user']['exec']['command']=str(wrapper)
    cfg=PRIVATE/'kubeconfig.yaml';cfg.write_text(yaml.safe_dump(kube));cfg.chmod(0o600)
    cloud.save('cluster-tunnel',{'target_ip':ip,'target_port':int(port),'local_port':LOCAL_PORT})
    print(json.dumps({'session_state':session.lifecycle_state,'kubeconfig':str(cfg)}))

def status():
    data=json.loads((PRIVATE/'bastion-session.json').read_text())
    session=cloud.client(oci.bastion.BastionClient).get_session(data['id']).data
    cloud.save('bastion-session',session)
    print(json.dumps({'state':session.lifecycle_state}))

def tunnel():
    session=json.loads((PRIVATE/'bastion-session.json').read_text())
    live=cloud.client(oci.bastion.BastionClient).get_session(session['id']).data
    assert live.lifecycle_state=='ACTIVE'
    target=json.loads((PRIVATE/'cluster-tunnel.json').read_text())
    host=f"host.bastion.{cloud.config['region']}.oci.oraclecloud.com"
    subprocess.run(['ssh','-i',str(KEY),'-N','-L',f"127.0.0.1:{target['local_port']}:{target['target_ip']}:{target['target_port']}",
        '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
        '-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile="{PRIVATE / "known_hosts"}"',
        '-p','22',f"{live.id}@{host}"],check=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','status','tunnel']);args=parser.parse_args()
    {'prepare':prepare,'status':status,'tunnel':tunnel}[args.action]()
