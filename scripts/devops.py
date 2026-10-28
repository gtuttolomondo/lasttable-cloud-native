"""Run and inspect the demo's real OCI DevOps pipelines; private evidence only."""
import argparse
import json
from datetime import datetime, timedelta, timezone
import oci
import cloud


def outputs():
    return json.loads((cloud.PRIVATE/'terraform-state.json').read_text())['outputs']['devops']['value']


def start_build(label):
    api=cloud.client(oci.devops.DevopsClient)
    details=oci.devops.models.CreateBuildRunDetails(
        build_pipeline_id=outputs()['build_pipeline_id'],display_name='LastTable '+label,
        build_run_arguments=oci.devops.models.BuildRunArgumentCollection(items=[
            oci.devops.models.BuildRunArgument(name='RELEASE_LABEL',value=label)]))
    data=api.create_build_run(details).data
    cloud.save('latest-build',data)
    print(json.dumps({'id':data.id,'state':data.lifecycle_state}))


def status(kind):
    api=cloud.client(oci.devops.DevopsClient)
    saved=json.loads((cloud.PRIVATE/('latest-'+kind+'.json')).read_text())
    data=(api.get_build_run(saved['id']) if kind=='build' else api.get_deployment(saved['id'])).data
    raw=oci.util.to_dict(data)
    cloud.save('latest-'+kind,data)
    progress=raw.get('build_run_progress') or raw.get('deployment_execution_progress') or {}
    print(json.dumps({'id':data.id,'state':data.lifecycle_state,'detail':raw.get('lifecycle_details'), 'progress':progress},indent=2))
    if kind=='build':
        deployments=api.list_deployments(deploy_pipeline_id=outputs()['deploy_pipeline_id']).data.items
        if deployments:
            latest=max(deployments,key=lambda item:item.time_created)
            cloud.save('latest-deploy',api.get_deployment(latest.id).data)
            print(json.dumps({'deployment_id':latest.id,'state':latest.lifecycle_state}))


def logs():
    state=json.loads((cloud.PRIVATE/'terraform-state.json').read_text())
    group=next(r['instances'][0]['attributes']['id'] for r in state['resources'] if r['type']=='oci_logging_log_group')
    log=next(r['instances'][0]['attributes']['id'] for r in state['resources'] if r['type']=='oci_logging_log' and r['name']=='devops')
    api=cloud.client(oci.loggingsearch.LogSearchClient)
    now=datetime.now(timezone.utc)
    query='search "'+cloud.COMPARTMENT+'/'+group+'/'+log+'" | sort by datetime desc'
    result=api.search_logs(oci.loggingsearch.models.SearchLogsDetails(
        time_start=now-timedelta(hours=2),time_end=now,search_query=query,is_return_field_info=False),limit=1000).data
    cloud.save('devops-service-logs',result)
    for row in reversed(result.results or []):
        item=row.data
        content=item.get('logContent',{}); detail=content.get('data',{})
        print(json.dumps({'time':content.get('time'),'run':detail.get('buildRunId') or detail.get('deploymentId'),'message':detail.get('message')}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build','status','logs']);p.add_argument('--kind',choices=['build','deploy'],default='build');p.add_argument('--label',default='v1');args=p.parse_args()
    if args.action=='build':start_build(args.label)
    elif args.action=='status':status(args.kind)
    else:logs()
