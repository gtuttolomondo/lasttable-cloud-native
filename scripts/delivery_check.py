"""Check actual DNS and Email Delivery readiness without submitting email."""
import json
import subprocess
import oci
import cloud

state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
out=state['outputs']['delivery']['value']
variables=json.loads((cloud.PRIVATE/'variables.json').read_text())
domain=variables['demo_domain']
api=cloud.client(oci.email.EmailClient)
email_domain=api.get_email_domain(out['email_domain_id']).data
dkim=api.get_dkim(out['dkim_id']).data
senders=oci.pagination.list_call_get_all_results(api.list_senders,compartment_id=cloud.COMPARTMENT).data
sender=api.get_sender(next(s.id for s in senders if s.email_address==out['sender'])).data
assert email_domain.lifecycle_state=='ACTIVE' and dkim.lifecycle_state=='ACTIVE'
assert sender.lifecycle_state=='ACTIVE' and email_domain.is_spf and sender.is_spf
report={'email_domain':'ACTIVE','dkim':'ACTIVE','sender':'ACTIVE','spf':True,'public_dns':{}}
for resolver in ['1.1.1.1','8.8.8.8']:
    def query(name,kind):
        result=subprocess.run(['dig','@'+resolver,'+time=5','+tries=1','+short',name,kind],check=True,capture_output=True,text=True)
        return result.stdout.strip().splitlines()
    assert query(out['hostname'],'A')==[out['public_ip']], 'Application public A record differs'
    actual=query(dkim.dns_subdomain_name,'CNAME')
    assert actual and actual[0].rstrip('.')==dkim.cname_record_value.rstrip('.')
    assert any('include:eu.rp.oracleemaildelivery.com' in s for s in query(domain,'TXT'))
    assert any('v=DMARC1;' in s for s in query('_dmarc.'+domain,'TXT'))
    report['public_dns'][resolver]='A, DKIM, SPF and DMARC passed'
cloud.save('delivery-verification',report)
print(json.dumps(report))
