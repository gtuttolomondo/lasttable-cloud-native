"""Create a deterministic, credential-free companion source ZIP without OCI access."""
from pathlib import Path
import hashlib
import json
import zipfile
from source_inventory import files, check

root=Path(__file__).resolve().parents[1]
target=root/'output/lasttable-source.zip'
target.parent.mkdir(exist_ok=True)
manifest={}
with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for file in files(root):
        check(file.read_text())
        name=str(file.relative_to(root));content=file.read_bytes()
        info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
        archive.writestr(info,content)
        manifest[name]=hashlib.sha256(content).hexdigest()
    info=zipfile.ZipInfo('source-manifest.json',date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
    archive.writestr(info,json.dumps({'format':1,'files':manifest},indent=2))
with zipfile.ZipFile(target) as archive:assert archive.testzip() is None
print(json.dumps({'source_files':len(manifest),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'private_material':'excluded'}))
