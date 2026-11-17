"""Read authoritative OCIR digests only after native artifact delivery succeeds."""
import json
import os
from pathlib import Path
import shlex
import time
import oci

built=json.loads(Path('.release/input-release.json').read_text())
signer=oci.auth.signers.get_resource_principals_signer()
region=os.environ.get('OCI_RESOURCE_PRINCIPAL_REGION') or signer.region
api=oci.artifacts.ArtifactsClient({'region':region},signer=signer)
compartment=os.environ['COMPARTMENT_OCID'];project=os.environ['PROJECT_NAME']
assert not (built.get('probe_only') and os.environ.get('CI_PRODUCTION_DEPLOY_ENABLED','false').lower()=='true'),'Probe release cannot be deployed to production'
exports={'RELEASE':built['RELEASE']}
for role in ['backend','frontend','notifications']:
    tag=built[role.upper()+'_IMAGE_TAG']
    repository=project+'/'+role
    expected=repository+':'+built['RELEASE']
    actual=None
    for _ in range(45):
        images=oci.pagination.list_call_get_all_results(api.list_container_images,compartment_id=compartment,repository_name=repository).data
        actual=next((image for image in images if image.display_name==expected),None)
        if actual:break
        time.sleep(2)
    assert actual,'Delivered OCIR image metadata not yet available'
    if role=='notifications':
        exports['FUNCTION_IMAGE']=tag;exports['FUNCTION_DIGEST']=actual.digest
    else:exports[role.upper()+'_IMAGE']=tag.rsplit(':',1)[0]+'@'+actual.digest
Path('.release/release.json').write_text(json.dumps({**exports,**{k:built[k] for k in ['source_commit','build_run_id','baseline_commit','verification_mode','application_files','application_manifest_sha256','frontend_assets','probe_only']}},indent=2)+'\n')
Path('.release/export.sh').write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in exports.items()))
print(json.dumps({'release':exports['RELEASE'],'authoritative_ocir_digests':'verified','private_images':3}))
