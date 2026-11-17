# Native OCI CI/CD and build provenance

The canonical build source is the OCI DevOps repository branch `codex/implementation`.
The public GitHub repository mirrors reviewed source. A GitHub push is not an OCI
build trigger. The OCI push trigger is enabled only after native validation.

The pipeline builds/tests three images on the OCI managed runner, publishes them
with native Deliver Artifacts, resolves authoritative OCIR digests, then triggers
the selected deployment pipeline. No personal API key is required on the runner.

## Application identity

Normal builds use the checked-out Git commit, including new application and frontend
changes. CI records hashes of all tracked inputs under app/bootstrap/frontend/notifications,
rejects uncommitted input changes or mismatched commit provenance, then builds and tests.
Backend/Function installed packages must match their current requirements.lock.txt;
npm uses package-lock.json. Deliberate dependency upgrades update these lock files.
The compiled frontend asset hashes are recorded for the new release; they do not have
to match an older release. Mail text changes require no baseline/hash maintenance.

`ci/application-baseline.json` remains a historical reference for the original local
release. Only the explicit diagnostic command `python3 ci/build.py --verify-release-baseline`
requires old source, package and frontend hashes to match. The normal build specification
does not enable this diagnostic. Image timestamps/provenance labels can change image
digests even when application inputs match.

The release manifest carries the source commit, OCI build run, verification mode,
current input hashes and compiled asset hashes with the three remote image references.
The historical baseline commit is set only when that diagnostic was requested. Publication
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

A later mail-text edit exposed that enforcing the old release hashes in every build
blocked normal development. Normal builds now validate the current commit and lock
files; eight CI regression tests cover changed mail, new source files, dirty/mismatched
inputs, lock-file drift and changed frontend assets. Historical matching is opt-in.

The reviewed activation plan changes only the build pipeline's production flag,
the deployment trigger destination and creation of the OCI source push trigger.
It creates or replaces no cluster, database, Kafka or network resources. Production
activation is a separate operator-approved action; the default flags remain false.
