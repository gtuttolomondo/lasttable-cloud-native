# Native OCI CI/CD and released-application baseline

The canonical build source is the OCI DevOps repository branch `codex/implementation`.
The public GitHub repository mirrors reviewed source. A GitHub push is not an OCI
build trigger. The OCI push trigger is enabled only after native validation.

The pipeline builds/tests three images on the OCI managed runner, publishes them
with native Deliver Artifacts, resolves authoritative OCIR digests, then triggers
the selected deployment pipeline. No personal API key is required on the runner.

## Application identity

`ci/application-baseline.json` records hashes from the locally released application
and frontend assets plus the installed Python package versions. Application source
files are checked before any build; backend/Function dependencies and compiled
frontend assets must match. Container recipes pin the exact released public bases;
Python lock files capture transitive packages and npm uses its existing lock file.
New image timestamps/provenance labels can change image digests; these checks prove
application/source/dependency/asset equivalence, not bit-identical container archives.
Update this baseline deliberately with an approved application change, after local
validation; CI-only changes must leave application hashes untouched.

The release manifest carries the source commit, OCI build run, baseline commit and
application-manifest hash together with the three remote image references. Publication
uses static local image names and dynamic unique remote tags. Deployments consume
the verified digests. Failed builds/tests cannot reach deployment.

## Validation switches

`ci_production_deploy_enabled=false` routes the build trigger to a five-second WAIT
pipeline with no workload stages. `CI_PROBE_ONLY=true` builds tiny images with a real
layer to test all three native publication targets. A probe refuses to run when
production deployment is enabled. Probe success does not count as application tests.

After native probe and full application build have passed, enable production routing
and the OCI source push trigger through Terraform. Keep the existing Resource Manager
stack and state. Review the plan for pipeline-only changes; do not recreate the cluster.
The pre-existing operator-managed Bastion CIDR drift is outside this CI repair.

The routing flag is checked against the live build pipeline defaults; it cannot be
overridden on a single run to let probe images reach a production-enabled pipeline.

## Registry authorization

Native delivery reproduced the custom upload's authorization failure at the first
Docker layer upload. Direct READ/UPDATE permission on the exact build dynamic group
was also insufficient. Adding REPOSITORY_CREATE to the same three repository names
allowed native image publication. The grant is scoped to the application compartment,
the exact build pipeline dynamic group and backend/frontend/notifications repository
names. It grants no REPOSITORY_MANAGE or REPOSITORY_DELETE permission. Existing `read
repos` permission supports metadata queries. No personal registry token is used in CI.

Database schema migrations remain the explicit ordered operator/bootstrap workflow
documented in stack-release.md. The released application baseline includes migration 3.

## Verified result, 17 November 2026

The full native application run from source commit `f9a63ba3c7617d9d7781223283f89f3011fbe20e`
completed successfully as release `local-equivalent-3au7qyhxaxgq`: three images built
and tested, three native OCIR deliveries, authoritative digest resolution and the
no-op validation deployment all succeeded. Source checks covered 43 application
files; runtime checks matched 42 backend and 38 Function Python packages and all
three frontend assets from the local release. All 30 backend tests and the Function
wire-protocol probe passed.

The reviewed activation plan changes only the build pipeline's production flag,
the deployment trigger destination and creation of the OCI source push trigger.
It creates or replaces no cluster, database, Kafka or network resources. Production
activation is a separate operator-approved action; the default flags remain false.
