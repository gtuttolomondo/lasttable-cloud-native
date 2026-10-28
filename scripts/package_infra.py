"""Build a deterministic, allowlisted Resource Manager archive."""
from pathlib import Path
import hashlib
import json
import zipfile
from source_inventory import files, check

root = Path(__file__).resolve().parents[1]
source = root / 'infra'
target = root / 'output' / 'lasttable-oci-resource-manager.zip'
target.parent.mkdir(exist_ok=True)
allowed = []
for p in source.rglob('*'):
    relative = p.relative_to(source)
    if not p.is_file() or '.terraform' in relative.parts:
        continue
    if p.suffix == '.tf' or p.name in ['schema.yaml', '.terraform.lock.hcl', 'README.md'] or (relative.parts[0] == 'assets' and p.suffix in ['.py', '.tftpl', '.yaml', '.json']):
        allowed.append(p)
assert (source/'main.tf') in allowed and (source/'schema.yaml') in allowed
entries = {str(p.relative_to(source)):p for p in allowed}
# Ship the complete companion application, migrations and operator scripts inside the stack.
for p in files(root):
    check(p.read_text())
    entries['application/'+str(p.relative_to(root))]=p
manifest = {'format':1,'files':{name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in sorted(entries.items())}}
with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name,p in sorted(entries.items()):
        info=zipfile.ZipInfo(name, date_time=(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        info.external_attr=0o100644 << 16
        archive.writestr(info,p.read_bytes())
    archive.writestr(zipfile.ZipInfo('manifest.json',date_time=(2026,1,1,0,0,0)),json.dumps(manifest,indent=2))
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    assert not any(x.endswith(('.tfvars','.tfstate','.pem')) for x in archive.namelist())
print(json.dumps({'archive':str(target),'files':len(entries)+1,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}))
