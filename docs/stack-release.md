# Consolidated LastTable stack

The release includes manual booking decisions and restaurant editing (`/manage`),
receipt plus final-decision HTML emails, live popularity on the booking homepage,
operational charts/traces and the interactive Kafka event explorer (`/demo`).
The Kafka explorer exposes only an allowlisted public projection of up to 100
published session events, with durable consumer effects; it never consumes a topic
or moves offsets. DLQ bodies and customer contact details remain private.

## Package layout

The GitHub repository contains the reviewed source and Terraform under `infra/`.
Release assets contain `lasttable-oci-resource-manager.zip` (Terraform root plus
complete companion source in `application/`) and `lasttable-source.zip`. Both have
SHA-256 file manifests. State, private variables, credentials, operational evidence,
local mounts and build caches are excluded.

## Existing stack upgrade

1. Use the existing Resource Manager stack and its state. Retain its variables and
   `deployment_stage=application`; do not create another owner for the same resources.
2. Upload the new Resource Manager ZIP. Keep `table_admin_secret_name` pointing to the existing administration Secret.
   Do not supply the code as a Terraform variable. Set tested backend/frontend image digests and notification
   image/tag plus digest. Do not store environment values in GitHub.
3. Run a Resource Manager Plan and review the complete plan. Confirm no cluster,
   database, Kafka or network replacement/deletion. Apply that exact successful plan
   when ready to activate the Terraform consolidation. Stack upload alone is not Apply.
4. Refresh private local references from Resource Manager outputs/state using the
   existing operator setup and reapply the operator foundation with
   `scripts/registry_foundation.py` if its references changed.
5. Preserve the existing `table-admin-access` Secret in namespace `lasttable`.
   For a new environment, provision a Secret with key `code` privately before the
   API deployment. `scripts/admin_release.py` supports the existing demo's private
   operator setup. Never commit the actual code or add it to Resource Manager variables.
6. Check migration 2 with `scripts/admin_migration.py` (requires the existing bootstrap
   configuration/image and private cluster access). Migration 1 and migration 2 are
   immutable and do not drop bookings or recreate tables.
7. Push reviewed source with `scripts/source_export.py push`, build/test the containers,
   publish immutable OCIR images and run `scripts/deployment.py start` with the release
   image file. The cluster is reused. Check DevOps completion and application behavior.

## Fresh environment

Provision the cumulative Terraform stages described in `infra/README.md`. Run the
versioned private database/Kafka bootstrap before the first application deployment.
`bootstrap/verify_data.py` applies both schema migrations in order. Build and publish
images in your own private OCIR namespace, supply image references and provision the administration Secret privately, install the foundation and run the DevOps deploy pipeline. This package
contains all source but is not a single Terraform Apply that builds containers or
initializes SQL/Kafka automatically.

## Verification and delivery

Run the application unit tests, `npm --prefix frontend run build`, Terraform
`fmt -check`/`validate`, the read-only observability probe where available, and the
package integrity/private-value scan (`scripts/release_check.py`). Existing tests
cover manual approval, duplicate protection, HTML escaping, receipts, public data
projection and partition-local lag. Runtime SQL/Kafka probes use existing identities.

OCI managed-build image publication currently fails with OCIR HTTP 403 in the demo
tenancy. Local tested builds plus OCIR publish and the OCI DevOps deploy pipeline
are the verified release route. The package preserves the native build definition
without claiming that unresolved IAM/registry issue is fixed.
