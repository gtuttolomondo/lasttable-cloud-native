"""Validate archive manifests and check actual private values never entered releases."""
import base64
import hashlib
import json
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
private=root/'.local'
values=set()
state_path=private/'terraform-state.json'
if state_path.exists():
    for resource in json.loads(state_path.read_text()).get('resources',[]):
        keys=['result'] if resource['type']=='random_password' else ['username','password'] if resource['type']=='oci_identity_smtp_credential' else []
        for instance in resource.get('instances',[]):
            for key in keys:
                value=instance.get('attributes',{}).get(key)
                if isinstance(value,str) and len(value)>=16:values.add(value)
for name in ['local-secrets.json','test-recipient.json','table-admin-code.json']:
    path=private/name
    if path.exists():
        for value in json.loads(path.read_text()).values():
            if isinstance(value,str) and len(value)>=6:values.add(value)
needles={variant for value in values for variant in [value.encode(),base64.b64encode(value.encode())]}
reports=[]
for filename,manifest_name in [('lasttable-oci-resource-manager.zip','manifest.json'),('lasttable-source.zip','source-manifest.json')]:
    path=root/'output'/filename
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest=json.loads(archive.read(manifest_name))['files']
        assert set(archive.namelist())==set(manifest)|{manifest_name}
        for name,digest in manifest.items():
            parts=Path(name).parts
            assert not set(parts)&{'.local','.tools','.git','.terraform','node_modules','__pycache__'}
            content=archive.read(name)
            assert hashlib.sha256(content).hexdigest()==digest
            assert not any(value in content for value in needles),'Environment secret/contact found in release archive'
        reports.append({'archive':filename,'files':len(manifest),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
print(json.dumps({'manifest_checks':'passed','actual_private_values_checked':len(values),'leaks':0,'archives':reports},indent=2))
