"""Private ADB development tunnel. Credentials remain in ignored runtime mounts."""
import argparse
import json
import subprocess
import oci
import cloud

def prepare():
    cfg=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
    outputs=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())['outputs']
    client=cloud.client(oci.bastion.BastionClient)
    path=cloud.PRIVATE/'database-bastion-session.json'
    session=None
    if path.exists():
        candidate=client.get_session(json.loads(path.read_text())['id']).data
        if candidate.lifecycle_state in ['ACTIVE','CREATING']:session=candidate
    if session is None:
        session=client.create_session(oci.bastion.models.CreateSessionDetails(
            display_name='lasttable-private-adb',bastion_id=outputs['security']['value']['bastion_id'],
            target_resource_details=oci.bastion.models.CreatePortForwardingSessionTargetResourceDetails(
                session_type='PORT_FORWARDING',target_resource_private_ip_address=cfg['database_private_ip'],target_resource_port=1521),
            key_type='PUB',key_details=oci.bastion.models.PublicKeyDetails(public_key_content=(cloud.PRIVATE/'bastion-ssh.pub').read_text()),
            session_ttl_in_seconds=10800)).data
    cloud.save('database-bastion-session',session)
    print(json.dumps({'state':session.lifecycle_state}))

def tunnel():
    data=json.loads((cloud.PRIVATE/'database-bastion-session.json').read_text())
    session=cloud.client(oci.bastion.BastionClient).get_session(data['id']).data
    assert session.lifecycle_state=='ACTIVE'
    cfg=json.loads((cloud.PRIVATE/'bootstrap-config.json').read_text())
    subprocess.run(['ssh','-i',str(cloud.PRIVATE/'bastion-ssh'),'-N',
        '-L',f"127.0.0.1:11521:{cfg['database_private_ip']}:1521",
        '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
        '-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile="{cloud.PRIVATE / "known_hosts"}"',
        f"{session.id}@host.bastion.{cloud.config['region']}.oci.oraclecloud.com"],check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','tunnel']);a=p.parse_args()
    {'prepare':prepare,'tunnel':tunnel}[a.action]()
