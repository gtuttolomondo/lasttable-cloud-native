"""Shared, offline inventory and checks for publishable source."""
import re
from pathlib import Path
DIRECTORIES=['docs','app','bootstrap','ci','deploy','frontend','infra','notifications','scripts','tests']
ROOT_FILES=['.gitignore','.containerignore','.dockerignore','README.md']
ALLOWED_SUFFIXES={'.py','.txt','.sql','.tf','.yaml','.yml','.json','.js','.jsx','.css','.html','.md','.tftpl'}
EXCLUDED={'.terraform','__pycache__','node_modules','dist','.release','.venv'}


def files(root):
    result=[]
    for directory in DIRECTORIES:
        for file in (root/directory).rglob('*'):
            relative=file.relative_to(root)
            if not file.is_file() or set(relative.parts)&EXCLUDED:continue
            if file.suffix not in ALLOWED_SUFFIXES and file.name not in ['Containerfile','Caddyfile','Caddyfile.https','.terraform.lock.hcl']:continue
            if file.name.endswith(('.tfvars','.tfstate')) or '.env' in file.name:continue
            result.append(file)
    result.extend(root/name for name in ROOT_FILES if (root/name).exists())
    return sorted(result)


def check(content):
    assert not re.search(r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',content),'Private key material in source export'
    assert not re.search(r'ocid1\.[a-z]+\.oc1(?:\.[a-z0-9-]+)?\.[a-z0-9]{25,}',content),'Environment OCID embedded in source export'
    assert not re.search(r'\b[A-Za-z0-9._%+-]+@oracle[.]com\b',content),'Personal Oracle contact in source export'
