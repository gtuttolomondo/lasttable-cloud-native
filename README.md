# LastTable — From Code to Cloud on OCI

An illustrative restaurant booking application for an interactive cloud native webinar. Participants book from their phones; the public `/demo` page shows sanitized events, Kafka consumer lag, notification outcomes and an independently rebuilt ranking. `/presenter` requires a separate bearer token.

## Architecture

- React and Caddy serve the mobile booking interface and dashboard on OKE.
- FastAPI stores a booking and its outbox event in one Autonomous Database transaction.
- An OKE publisher sends the outbox to OCI Streaming with Apache Kafka and marks it published only after broker acknowledgment.
- The intake consumer records that a request is awaiting a decision. An authenticated administrator confirms or rejects it in `/manage`; the decision and `booking.status_changed` outbox event commit atomically.
- Independent consumers maintain the restaurant ranking and invoke the notification Function.
- The Function looks up the recipient through a restricted database view and submits mail over authenticated TLS to OCI Email Delivery.
- An observer reads Kafka offsets; a replay worker rebuilds a separate ranking without invoking notifications.

Kafka messages and the public dashboard exclude email addresses and free text names. `SUBMITTED` means SMTP accepted the message; it does not assert mailbox delivery. An ambiguous SMTP DATA outcome becomes `UNKNOWN` and is not automatically resent.

## Source layout

| Path | Purpose |
|---|---|
| `infra/` | Terraform root, local network module and Resource Manager schema |
| `bootstrap/` | Versioned SQL schema, Kafka topics/users/ACL setup and verification |
| `app/` | API, outbox, consumers, observer, replay, notification core and tests |
| `notifications/` | Oracle Functions FDK adapter and minimal image |
| `frontend/` | React interface and rootless Caddy image |
| `ci/` | OCI DevOps build specifications and authoritative digest verification |
| `scripts/` | Staged cloud operations, private connections, packaging and source export |

## Deployment sequence

Use one Resource Manager stack throughout. The stages are cumulative: `network`, `security`, `platform`, `data`, `delivery`, `application`. Never decrease the stage on an existing stack. Run Plan, review it, Apply that successful plan, verify actual resources, and require a subsequent no-change Plan before advancing.

1. Package `infra/` with `python3 scripts/package_infra.py`. Upload `output/lasttable-oci-resource-manager.zip` into Resource Manager using Terraform 1.5.x.
2. Complete networking, Vault/Bastion, OKE/OCIR, then private ADB and managed Kafka. Bootstrap schema, least-privilege database users, topics and SCRAM ACLs from inside the private network.
3. Build and test images locally with Podman for `linux/amd64`, publish privately to OCIR and record authoritative digests. Supply those image references to the delivery/application stages.
4. Delegate the registered domain to the new OCI DNS zone; verify DKIM, SPF and approved sender readiness. Verify Function database/SMTP authentication without sending mail.
5. Install the operator foundation with `scripts/registry_foundation.py`. It installs runtime service accounts, configuration, a registry credential refresher and the deployment pipeline's minimal namespace Role. The deployment identity can release application Deployments, Services and the certificate PVC; it cannot read secrets or change RBAC. Run the initial registry refresh before deploying private images.
6. Push the allowlisted source into the temporary private OCI repository with `scripts/source_export.py push`. Run the OCI DevOps build pipeline. It builds and tests three images, publishes them through native Deliver Artifacts, verifies their OCIR digests and passes those digests to the deployment pipeline.
7. DevOps releases the Function and applies the OKE manifests through the private deployment environment. Caddy obtains a certificate after DNS and the public load balancer are ready.
8. Verify one authorized real email separately from SMTP acceptance, then rehearse duplicate events, consumer pause/resume and isolated replay.

Cloud scripts use the OCI CLI's Python environment, which includes the OCI SDK. They write environment configuration and evidence only under ignored `.local/`. `LASTTABLE_ENV` selects an isolated `.local/<name>` directory; `LASTTABLE_COMPARTMENT_OCID` and `LASTTABLE_STACK_NAME` can select a verification environment. Run `--help` for individual entry points.

## Verification

Contract tests run inside the built backend image. Local integration tests use Kafka and Mailpit in Podman with a real private ADB reached over verified TLS through a temporary Bastion tunnel. The acceptance suite verifies transaction atomicity, HTTP/Kafka/Function duplicate handling, pause/lag recovery, safe retries, ambiguous SMTP outcomes, replay equality and public data filtering.

Do not run local publishers/consumers against the same database while cloud workers are active: they would compete for the same outbox and send events to different brokers.

Verification on 2026-10-27: real DevOps deployments, version recovery, public HTTPS/mobile UI, an authorized mailbox delivery, 30 concurrent idempotent retries, Kafka redelivery, malformed-event DLQs and isolated replay passed. A separate environment was recreated and exercised end to end with notifications paused and zero SMTP attempts, then its 135 resources were destroyed through Resource Manager with explicit approval; its Vault remains scheduled for deletion under OCI retention. Managed CI publication is still under investigation: OCIR denied writes both with repository-name filters and with compartment-wide READ/UPDATE permissions. The approved policy now matches the exact build pipeline identity in either principal context; live verification is pending. No public GitHub repository has been published.

## Credentials and release boundaries

Two tenancy-level IAM dynamic groups match the exact build and deploy pipeline OCIDs; their policies grant access in the demo compartment. All application infrastructure is provisioned in that compartment.

Runtime access uses OKE Workload Identity or Function Resource Principals and per-role Vault permissions. No user API key belongs in an image or pod. SMTP credentials and database passwords stay in Vault. Terraform state can contain generated secrets: restrict access to Resource Manager state and private evidence.

The infrastructure ZIP and source export use explicit allowlists. They exclude local credentials, Terraform state/plans, database wallets, recipient information, node modules and caches. Public GitHub publication is a later step and is not performed by these scripts.

## Cleanup

Stop builds and deployment runs first. Remove the application LoadBalancer Service and PVC intentionally, accounting for any data or certificate retention needed. Confirm the service-created load balancer has detached the reserved address before destroying infrastructure. Review a Resource Manager Destroy plan for the intended stack only. Keep the existing compartment; it is outside the stack. Remove temporary Bastion sessions and local test containers when no longer needed.

The one-broker Kafka development cluster and single application replicas are chosen for demonstration. Provisioned OKE nodes, ADB, Kafka, load balancer, NAT, storage and logs incur charges until removed.

## Restaurant administration and consolidated stack

`/manage` provides the **Gestisci Tavoli** tab: current-session bookings with private customer details, individual/selected/all-pending confirmation or rejection, and restaurant creation/editing. New requests remain PENDING until an administrator decides. Decisions are final and repeated submissions skip previously decided requests. Both confirmed and rejected outcomes generate an email through the existing outbox/Kafka/Function flow.

Access uses `LASTTABLE_TABLE_ADMIN_CODE` from the existing `table-admin-access` Kubernetes Secret. Terraform now includes this Secret reference directly in the API deployment manifest, parameterized by `table_admin_secret_name`. The actual code is provisioned privately by the operator and never enters GitHub, archive files or Resource Manager variables. The presenter token remains separate.

The consolidated Terraform manifest includes the API rolling update and admin Secret reference, so no development manifest overlay is needed after the stack update. New environments run `bootstrap/verify_data.py`, which applies immutable schema migration 1 followed by migration 2 (`bootstrap/migrate_admin.py`). Existing environments use `scripts/admin_migration.py` to apply/check migration 2 without recreating tables. Provision the access Secret privately before deploying the API; `scripts/admin_release.py` supports this operator step for the existing demo.

The Resource Manager ZIP contains the complete source under `application/`, including containers, frontend, HTML email templates, migrations, tests and operational scripts. Terraform resources are at the archive root. Database/Kafka initialization, operator foundation installation and application image deployment remain explicit ordered operational steps; Terraform Apply alone does not execute these steps. See [stack upgrade and reproduction](docs/stack-release.md).

`app/tests/admin_database_probe.py` exercises the actual restricted Oracle identity and administrative routes within a rolled-back transaction: no test bookings are retained and no email is sent.

## Receipt notification

New bookings atomically queue both `booking.created` for intake/ranking and `booking.received` for the receipt email. Receipt and final-decision notifications share the existing status-changed Kafka topic, keyed by booking ID, and have separate event IDs and delivery ledger entries. The receipt does not confirm a booking. HTTP retries reuse the original UUID and do not queue another email. Existing bookings receive no retroactive receipt.

The booking status API exposes `acknowledgement` separately from the final `notification`. SMTP totals include both receipt and decision emails. All three messages use a LastTable HTML template with inline styles and an equivalent plain-text alternative; customer and restaurant fields are HTML-escaped. No external image or font requests are needed to display the message.

## Live operational dashboard

`/demo` shows a live component map, the four allowlisted application topics, partition offset ranges, the three primary consumer groups, logical event counts for the current session, two historical charts, and a booking trace selected by anonymous alias. The trace endpoint is scoped to the current session and never returns names, email addresses, recipients or event payloads.

Session counts come from the Oracle outbox, processed-events and delivery ledgers. Kafka lag, committed offsets, topic watermarks and group members cover all sessions on the shared topics. Lag is calculated per partition; unknown commits remain unknown and out-of-range commits are flagged. The retained offset span is explicitly labelled as an interval, not an exact message count.

The observer uses existing DESCRIBE permissions only, sampling about every ten seconds. Compact snapshot records fit the existing 400-character diagnostic column. Topic snapshots are persisted on change and periodically refreshed, reducing repeated metadata writes. The API returns up to 120 lag snapshots and 15 one-minute buckets for publications/SMTP acceptances. No infrastructure recreation, schema migration or Kafka ACL expansion is required. Consumer/Function redeliveries that create no new effect are logged for the new deduplication counter.

`scripts/observability_probe.py` checks actual Oracle queries and Kafka observations read-only from existing pod identities before a release; `app/tests/test_observability.py` covers scope, privacy, timing and partition-local lag.
