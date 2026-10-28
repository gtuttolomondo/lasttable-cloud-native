# LastTable — Resource Manager package

This ZIP contains the Terraform root, local network module, pinned provider lock,
Resource Manager schema, deployment manifest generator, registry helper asset and
the full companion application source under `application/`.
It contains no state, credentials, wallets or environment-specific tfvars.

Upload to OCI Resource Manager, select Terraform 1.5.x, the existing target
compartment and region. Run Plan, review it, then Apply that successful plan.
Verify the real resources and require a second no-change Plan before advancing.
Resource Manager owns the state throughout; do not apply the same resources from
a second local Terraform state.

## Cumulative stages

| Stage | Adds |
|---|---|
| network | VCN, subnets, gateways, routes, security lists and NSGs |
| security | Vault, encryption key, log group, Bastion and service policy |
| platform | Private enhanced OKE cluster, node pool and private OCIR repositories |
| data | Private Autonomous Database, role-specific secrets and managed Apache Kafka |
| delivery | Public DNS, reserved ingress IP, Email Delivery, notification Function and workload policies |
| application | OCI DevOps project, private source repository, build/deploy pipelines, environments and manifest artifacts |

Never decrease `deployment_stage` on an existing stack. The compartment is an
input and is never created or destroyed by this package. The providers are pinned
to OCI 9.11.0, random 3.7.2 and time 0.13.1 with signed platform checksums.

## Network

The VCN defaults to 10.42.0.0/16. All subnets are regional:

| Subnet | Default CIDR | Access |
|---|---|---|
| ingress | 10.42.0.0/24 | Public HTTP/HTTPS load balancer |
| api | 10.42.1.0/24 | Private OKE endpoint |
| nodes | 10.42.16.0/20 | Private OKE nodes |
| database | 10.42.32.0/24 | Private Autonomous Database |
| kafka | 10.42.33.0/24 | Private managed Kafka TLS |
| functions | 10.42.34.0/24 | Function network attachment |
| automation | 10.42.35.0/24 | Private DevOps/Bastion access |

Security lists and NSGs are additive. Kafka has a subnet-specific list because
its service-managed VNICs are not assigned the application NSGs. Dedicated NSGs
define ingress, OKE API/node traffic, database TLS, private automation and SMTP.
Public SSH, database and Kafka ingress are not configured. A network settling
delay precedes Bastion creation because of observed subnet eventual consistency.
A separate settling delay follows Vault creation before creating its key because
the management endpoint DNS can appear after the Vault becomes ACTIVE.

## Prerequisites between stages

Data resources require bootstrap jobs from inside the private network: database
schema/users and Kafka topics, SCRAM users and ACLs are versioned in the companion
application source. A successful Terraform Apply alone does not initialize them.

Delivery requires tested private OCIR notification image/tag and authoritative
digest, a registered domain, and an existing IAM user with one free SMTP credential
slot. Delegate the registered domain to the OCI zone nameservers and verify DKIM
and SPF. Runtime secrets stay in Vault; generated secret values also appear in
Terraform state, so restrict Resource Manager state access.

Application requires tested backend/frontend digest references. Before its first
deployment, install the operator foundation from the companion source. It creates
runtime identities/configuration, registry refresh and a minimal namespace Role
for application Deployments, Services and the certificate PVC. The deployment
pipeline receives no secret or RBAC management. Run an initial credential refresh.
Then populate the temporary private OCI source
repository. The managed build tests images; native Deliver Artifacts publishes them and a
subsequent read-only step resolves their OCIR digests. DevOps owns later
Function image changes and Kubernetes application deployment. Terraform retains
ownership of infrastructure and pipeline definitions.

DevOps identity definitions are tenancy-level dynamic groups, each matching exactly
one pipeline OCID. Their access policies remain scoped to the demo compartment;
the Terraform provider discovers the identity home region automatically.
Allow for IAM dynamic group propagation before the first build; Oracle documents
that matching-rule changes can take about an hour. The build checks registry write
authorization before compiling images.

The current tenancy's managed-build OCIR write check was denied both with
repository-name conditions and with compartment-wide READ/UPDATE permissions;
removing the name filter alone did not resolve it. Runtime deployments of locally
tested OCIR images have passed; end-to-end managed CI publication remains
unverified. The approved compartment-wide image READ/UPDATE policy now matches
the exact build pipeline ID in either principal context and is undergoing live
verification. Repository creation, deletion and visibility changes are excluded.
The latest diagnostic confirms OCI API image metadata UPDATE succeeds and Docker
token scope includes pull,push, while upload returns 403 through both documented
regional aliases. The registry authorization decision remains unresolved; the
private support evidence records request IDs without credentials.

The public Service uses the reserved IPv4 address. Caddy obtains HTTPS certificates
after DNS and the load balancer are ready. Its certificate data uses an OKE block
volume through a dedicated paravirtualized StorageClass, compatible with node-pool
in-transit encryption. Public dashboard output omits recipient and free text personal fields.

## Verification and cleanup

Check lifecycle state and actual SQL, authenticated Kafka produce/consume,
negative ACL/secret access, image pull, Function invocation, HTTPS and a complete
booking flow. Verify SMTP acceptance separately from mailbox delivery.

Stop pipeline runs and intentionally remove the application LoadBalancer Service
and PVC before destroying the infrastructure. Confirm that the service-created
load balancer detached the reserved address. Review Resource Manager Destroy for
the intended stack only; retain the existing compartment. Compute, database,
Kafka, NAT, load balancer, storage and logging can incur charges while provisioned.

## Consolidated application release

All booking/admin features, receipt and decision HTML emails, homepage ranking,
observability and the interactive Kafka explorer are included in `application/`.
The `table_admin_secret_name` input references the existing Kubernetes Secret with
key `code` (default `table-admin-access`). The code itself is provisioned privately
by the operator before application deployment and never enters this archive or
Resource Manager variables. Terraform generates the API Secret reference and
rolling update strategy; a development manifest overlay is no longer required.

After an in-place stack upgrade, verify migration 2 and deploy the pinned tested
images through the existing DevOps pipeline. Keep the existing stack/state and
cumulative application stage. Uploading the new ZIP does not apply infrastructure
changes or recreate the cluster. Read `application/docs/stack-release.md` for the
ordered release procedure and managed-build OCIR publication limitation.
