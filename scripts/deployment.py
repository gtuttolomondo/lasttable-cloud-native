"""Deploy a tested release or cancel the tracked test deployment through OCI DevOps."""
import argparse
import json
import re
import oci
import cloud

p=argparse.ArgumentParser()
p.add_argument('action',choices=['start','cancel'])
p.add_argument('--label',default='initial')
p.add_argument('--release-file')
args=p.parse_args()
api=cloud.client(oci.devops.DevopsClient)
if args.action=='cancel':
    saved=json.loads((cloud.PRIVATE/'latest-deploy.json').read_text())
    actual=api.get_deployment(saved['id']).data
    if actual.lifecycle_state in ['ACCEPTED','IN_PROGRESS']:
        api.cancel_deployment(actual.id,oci.devops.models.CancelDeploymentDetails(reason='Correct empty certificate volume attachment type before retry'))
        print('Tracked deployment cancellation requested')
    else:print(actual.lifecycle_state)
else:
    assert re.fullmatch(r'[A-Za-z0-9_.-]{1,60}',args.label)
    state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
    pipeline=state['outputs']['devops']['value']['deploy_pipeline_id']
    values={'RELEASE':args.label}
    if args.release_file:
        release=json.loads(open(args.release_file).read())
        allowed={'BACKEND_IMAGE','FRONTEND_IMAGE','FUNCTION_IMAGE','FUNCTION_DIGEST'}
        assert allowed<=release.keys()
        values.update({key:release[key] for key in allowed})
        assert all('@sha256:' in values[k] for k in ['BACKEND_IMAGE','FRONTEND_IMAGE'])
        assert re.fullmatch(r'sha256:[a-f0-9]{64}',values['FUNCTION_DIGEST'])
    result=api.create_deployment(oci.devops.models.CreateDeployPipelineDeploymentDetails(
        deployment_type='PIPELINE_DEPLOYMENT',deploy_pipeline_id=pipeline,display_name=args.label,
        deployment_arguments=oci.devops.models.DeploymentArgumentCollection(items=[
            oci.devops.models.DeploymentArgument(name=k,value=v) for k,v in values.items()]))).data
    cloud.save('latest-deploy',result)
    print(json.dumps({'id':result.id,'state':result.lifecycle_state,'release':args.label}))
