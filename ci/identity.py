"""Read-only build identity checks. Native Deliver Artifacts owns registry writes."""
import json
import os
import oci
signer=oci.auth.signers.get_resource_principals_signer()
principal=signer.get_claim('res_id')
assert principal==os.environ['OCI_PIPELINE_ID'],'Unexpected build identity'
region=os.environ.get('OCI_RESOURCE_PRINCIPAL_REGION') or signer.region
devops=oci.devops.DevopsClient({'region':region},signer=signer)
pipeline=devops.get_build_pipeline(principal).data
defaults={item.name:item.default_value for item in pipeline.build_pipeline_parameters.items}
# This switch describes live Terraform routing, so a run argument must not override it.
assert os.environ.get('CI_PRODUCTION_DEPLOY_ENABLED','false')==defaults['CI_PRODUCTION_DEPLOY_ENABLED'],'Production routing flag cannot be overridden per build'
assert not (os.environ.get('CI_PROBE_ONLY','false').lower()=='true' and defaults['CI_PRODUCTION_DEPLOY_ENABLED']=='true'),'Probe images cannot enter a production-enabled pipeline'
api=oci.artifacts.ArtifactsClient({'region':region},signer=signer)
repositories=oci.pagination.list_call_get_all_results(api.list_container_repositories,compartment_id=os.environ['COMPARTMENT_OCID']).data
expected={os.environ['PROJECT_NAME']+'/'+role for role in ['backend','frontend','notifications']}
selected=[r for r in repositories if r.display_name in expected]
assert len(selected)==3 and all(not r.is_public for r in selected)
print(json.dumps({'build_identity':'verified','private_repositories':'verified','publication':'native Deliver Artifacts; no manual Docker upload'}),flush=True)
