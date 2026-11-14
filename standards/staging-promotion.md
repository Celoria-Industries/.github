# Staging promotion

This runbook describes a proposed shared GitHub workflow contract for
promoting reviewed container images and Terraform plans to staging.
**Both staging promotion paths are disabled in code:** the verification job
fails unconditionally before credentials, and the dependent writer job has
its own unconditional stop before credential configuration. No variable or
caller input enables them. Keep staging writer trust disabled. Removing the
guards requires a separately reviewed fix for the blockers below and real
acceptance evidence; bootstrap alone is not activation.

## Start here: you are setting up a customer's environment

**Current status: blocked, not an activated customer onboarding service.**
The shared code has local tests, but that is not evidence that it works with a
customer's AWS account, GitHub settings, or signing key. Activation requires
reviewed, merged, tested immutable workflow references and verified outputs
from the customer's bootstrap. Missing values mean **stop**, not guess.

This document covers approval and delivery into **staging**, a pre-production
environment. It does not create the customer's AWS account, network, cluster,
databases, or application deployments. Those belong to the infrastructure,
GitOps, and application owners' runbooks. Copying an image to the registry does
not itself start an application. Production is outside this runbook.

The examples below identify Celoria's repositories. The Terraform gate is
currently restricted to `Celoria-Industries/infrastructure-as-code`; changing
environment variables alone does not support a different customer's
repository. A new company needs reviewed code/configuration changes and its own
verified repository/account identities, not a wildcard trust policy.

### Terms you will encounter

| Term | Plain-language meaning |
|---|---|
| AWS member account | The customer's isolated AWS account within an organization. Its 12-digit ID identifies the destination; an account name is not sufficient. |
| Bootstrap account | The separate account holding shared setup resources, including Terraform state and the signing key in this design. It is not the staging deployment account. |
| PR (pull request) / review / merge | A PR proposes a repository change. A review records a person's approval of a particular revision. A merge adds that revision to `main`. These are different events, and none by itself proves deployment. |
| Workflow / job / run | A workflow is a GitHub Actions automation file; a job is an isolated part of it. A run is one execution. A rerun has a different attempt number, which must also match the evidence. |
| GitHub Environment | A named configuration context such as `staging-plan`, not an AWS account or a running cluster. Administrators configure its variables and access controls. |
| Commit SHA / pinned reference | An exact source revision. A full immutable SHA fixes the workflow version; `main` or a tag can change and is not an acceptable staging trust pin. |
| Image / ECR / digest | An image packages an application. ECR is AWS's image registry. A digest fingerprints the exact image; the intended promotion would copy it unchanged rather than rebuilding. |
| Terraform root / state / saved plan | The root is a stack's directory. State tracks managed infrastructure. A saved plan is the proposed changes captured in a file; applying it can create, change, or delete resources. |
| Artifact / evidence / manifest | An artifact is a stored workflow output. Evidence records its origin and fingerprints. The promotion manifest is the JSON file a human reviews to select the exact evidence and staging target. |
| IAM role / trust / OIDC subject | An IAM role grants AWS permissions. Its trust policy controls who can obtain those permissions. OIDC lets AWS check a short-lived GitHub identity, including the repository, environment, and pinned reusable workflow. It is not a stored AWS password. |
| KMS signature / public key / ARN | KMS keeps a private signing key in AWS. Its signature detects changes to the evidence; the public key verifies it without private-key access. An ARN is the exact AWS resource identifier. Signing does not approve the proposed infrastructure changes. |
| Fail closed | If a required check cannot be proved, stop without granting staging write access. Do not remove the check to make the run succeed. |

### Who authorizes what?

An AWS administrator performs the **human bootstrap**: account setup, IAM
permissions, and the KMS key. A repository administrator configures GitHub
environments, branch rules, and OIDC identity settings. These are reviewed
setup operations, not effects of approving a promotion PR.

A source reviewer approves code/configuration. Later, a different person from
the promotion PR author approves **which exact image or saved plan may go to
which staging target**. The source PR is not that later approval. GitHub Team
private repositories do not provide the required Environment-reviewer gate
used by some other plans; this design uses the reviewed promotion PR instead.
The experimental solo mode described below substitutes a single operator's
explicit authorizations and does not provide independent oversight.

Only an invocation of the configured promotion caller runs the shared gate.
Currently it stops before requesting staging write credentials. In a future
validated design, the intended job would copy the image or apply the saved
plan only after checks pass. Whether a caller is manually dispatched or
triggered by a repository event is defined in that repository; do not assume
clicking Approve or Merge runs deployment.

Planning reads state and AWS resources using scoped planning credentials.
Signing runs in a separate job with only signing permission. Applying runs in
a third context with deployment permission, after signature and approval
checks. Untrusted Terraform/providers never receive signing credentials.
These separations must exist in IAM, not just in the job names.

### Proposed setup and first-promotion checklist

**Do not enable or invoke staging promotion based on this checklist.** Steps 7
through 9 describe a future process, not an operational procedure. First
resolve the execution-boundary and transfer blockers below, remove the
fail-closed guards through review, and complete real-cloud acceptance.

Keep an onboarding record in the owning infrastructure runbook/ticket. Record
non-secret identifiers and links to evidence, not credentials or plan contents.
Complete each row before proceeding to the next.

| Order / owner | What to obtain or do | Evidence required before proceeding |
|---|---|---|
| 1. Operator with infrastructure owner | Identify the company, staging scope, repositories, region, and named AWS/repository/review owners. | Confirmed destination and ownership; no example IDs or unresolved placeholders. |
| 2. AWS administrator | Follow the infrastructure account/bootstrap runbook; create the reviewed account/state/role/key resources with staging writers disabled. | Human-applied stack outputs: member/bootstrap account IDs, state bucket, role ARNs, immutable key ARN and public key. Confirm these against AWS identity/resource records. |
| 3. Repository administrator | Confirm repository IDs, protected `main`, required checks, review access, and private artifact access. Inventory every AWS credential job before changing OIDC settings. | Current settings and inventory, including Dev/global jobs; compatible reusable jobs and exact non-staging trusts reviewed. Old rule snapshots below are not acceptance evidence. |
| 4. Delivery and IAM owners | Merge/review the central and caller workflows; select tested full workflow SHAs. Populate the environment variables listed below from verified bootstrap outputs and those SHAs. | Exact workflow paths/SHAs and variable-to-output mapping. Planner/signer target/root/key values agree with staging; no caller-selectable roles or targets. |
| 5. AWS/repository administrators | Follow the coordinated OIDC migration order below. Enable only the needed read-only planning and isolated signing trusts first; keep staging apply/image writers disabled. | Actual emitted subjects match repository ID, environment, and immutable workflow. Negative checks reject the wrong repo/environment/SHA and legacy staging identities; Dev/global identity checks still work. |
| 6. Signing and infrastructure owners | Verify the pinned public key and signing canary, state-to-member planning role chain, and a reviewed staging plan. Or, for images, verify a successful pinned main image producer and its digest. | Real-cloud acceptance record with run/attempt, account/root and artifact IDs/digests or image digest. Local unit tests alone are insufficient. |
| 7. Operator and qualified reviewer | Create the separate promotion PR containing only `.github/staging-promotion.json`. For Terraform, use the producer's generated fields and review the actual proposed changes, including deletions and permissions; the action-count summary alone is insufficient. | Current non-author human approval on the exact PR head, successful checks, merge, and matching source/run/target evidence. Never paste sensitive plan contents into a public record. |
| 8. AWS administrator, then operator | Future only: after the blockers are resolved and all prerequisites accepted, enable only exact staging writer trust and invoke the owning repository's configured caller. | Reviewed implementation and real-cloud acceptance demonstrate gate checks precede credential steps; record the Actions result and exact promoted digest or applied plan identity. |
| 9. Application/GitOps owner | Follow the service rollout and health-check runbooks after delivery. | Independently verified running services and environment acceptance. An ECR copy or Terraform success alone does not prove application readiness. |

Nothing here automatically creates accounts, changes GitHub settings, enables
IAM trust, retrieves a private key, starts production, or repairs a failed
deployment. The workflows perform their documented operations only when
invoked with configured, authorized identities.

## Trust boundary and approval

Promotion is not authorized by a source-code PR, a workflow dispatch, a
successful build, or a successful Terraform plan alone. A **separate PR** must
change only `.github/staging-promotion.json`, target `main`, be merged, have a
current human approval on its exact head from someone other than its author,
and have completed successful checks. The gate rechecks the PR, latest reviews,
checks, source evidence, and manifest immediately before requesting AWS
credentials. Stale approvals, failed/incomplete checks, automation-only
approvals, unexpected files, and mismatched evidence fail closed.
The intended gate verifies this promotion evidence but does not configure or prove the
target repository's branch-protection/ruleset settings; the repository owner
must verify protected `main` and its required checks before enabling staging
trust. API inspection during this change found an active `main` ruleset in
`Celoria-Industries/infrastructure-as-code` requiring the `Report PR plan
status` check and a PR, but its required approving review count is `0`;
`Celoria-Industries/service-template` has an active required-CI ruleset for
`Celoria Standard CI / CI` and `Celoria Standard CI / Required Validation`, and
`Celoria-Industries/frontend-template` currently has no ruleset. The gate
independently enforces a current human approval and successful checks on every
source and promotion PR, but the frontend owner must add/verify protected-main
rules before activation if protected main is a deployment prerequisite. Recheck
these settings immediately before enabling roles; this snapshot can become
stale.

The central reusable workflow accepts a `promotion-pr-number` and, in solo
mode, two authorization comment IDs. Its disabled prototype contains
copy/apply logic and selects roles and targets from
environment configuration. It does not expose a caller-controlled role,
command, script, artifact URL, Terraform root, signing key, or AWS operation.
This does not prevent Terraform configuration or provider code from executing
in the apply environment. IAM trust must prevent callers from bypassing the
gate; that trust is not established by this workflow alone.

Both caller workflows need only `actions: read`, `checks: read`,
`contents: read`, `pull-requests: read`, and `id-token: write`. The plan
producer uses the runtime artifact service for uploads; it does not request
repository `actions: write`. The reusable jobs narrow permissions further by
job; callers must grant the required permissions because reusable workflows
cannot elevate them.

The staging gate contract is:

| Workflow | Caller inputs | Reusable-workflow outputs |
|---|---|---|
| `staging-promotion-gate.yml` | `promotion-pr-number` (`number`, required); `source-authorization-comment-id` and `promotion-authorization-comment-id` (`string`, solo only) | `promotion-kind`, `source-commit`, `promoted-digest` (image only), `producer-run-id`, `plan-artifact-id`, `evidence-artifact-id` (Terraform only) |
| `staging-terraform-plan.yml` | `source-pr-number` (`number`, required); `plan-layer` (`string`, one of `foundation`, `nervous-system`, `service-bootstrap`, required); `source-authorization-comment-id` (`string`, solo only) | `plan-artifact-id`, `plan-artifact-digest`, `evidence-artifact-id`, `evidence-artifact-digest`, `plan-layer`, `terraform-root` |

No reusable-workflow input selects a role, signing key, target account,
Terraform root path, command, or artifact. The layer enum maps in trusted
central code to exactly one path under
`stacks/celoria-industries/staging/`; the source PR must carry exactly one
matching `staging-plan:<layer>` label. Missing, duplicate, or unknown layer
labels fail closed. Source validation, evidence creation and promotion
revalidation select the newest same-head `pull_request` run of the IaC
`terraform.yml` workflow (by latest attempt start time, then run ID), never an
older green run over a newer failed or pending attempt. Its latest attempt
must succeed, with matching GitHub Actions app/head/suite/job identities.
Discovery, validation, PR status summary, every nonstaging root's caller and
plan, and exactly the selected staging root's caller and plan must succeed.
Roots are enumerated from the exact source tree using the IaC matrix's
exclusions; incomplete tree or check evidence fails closed.
Only suites independently identified as superseded same-head runs of that
exact workflow are excluded. Other workflow/app check failures remain
blocking, and generic image/promotion-PR check validation is unchanged.
This makes the central saved plan selection agree with the IaC PR-CI selector.
The existing IaC pull-request planning behavior is owned by that repository
and is not changed here.

## Experimental solo authorization

The default mode remains `independent`: the existing non-author human reviews
are required. The separately configured `solo` mode accepts two issue comments
instead: one on the source PR and a later one on the promotion PR. Both
promotion paths remain unconditionally disabled, and configuring a mode does
not enable them. The comments are attributed to a pinned GitHub user ID, not
proof that a person was present or reviewed the changes: an API token for that
account can create them. Solo operation has no independent second reviewer and
does not protect against a compromised operator or mistaken approval.

The raw comment body must be an exact JSON object, without extra or duplicate
keys. After inspecting the source PR, the operator posts this on that PR:

```json
{"schema_version":1,"type":"source","repository_id":123,"pull_request":41,"head_sha":"<source PR head SHA>","target":"staging","account_id":"<staging account ID>","region":"<staging region>","plan_layer":"foundation","terraform_root":"stacks/celoria-industries/staging/foundation"}
```

After the image or plan evidence exists, the operator inspects it and posts
this separate comment on the promotion PR:

```json
{"schema_version":1,"type":"promotion","repository_id":123,"pull_request":42,"head_sha":"<promotion PR head SHA>","target":"staging","account_id":"<staging account ID>","region":"<staging region>","manifest_sha256":"sha256:<SHA-256 of exact manifest bytes>","target_resource":"<staging ECR registry/repository or Terraform root>","plan_layer":"foundation"}
```

Numbers above are examples, not customer values. The hash binds every manifest
field, including image digest or both plan/evidence IDs and digests, source,
producer run and attempt. The target resource and account/region are checked
separately against configured staging values. The source and promotion PRs
must still be merged to main with successful checks. Pass each comment's
numeric ID using the workflow inputs; a comment ID is not a permission grant.
For image authorization, omit `plan_layer` from the promotion comment. For a
Terraform source authorization, include the selected `plan_layer` and its exact
mapped `terraform_root`; Terraform promotion authorization includes the same
layer and root as `target_resource`.

The verifier checks GitHub's issue-comment ID, issue URL, user ID and type,
body, creation time, update time and GitHub App attribution. It rejects
missing/deleted comments, comments reporting an edit, extra fields, duplicate
keys, changed values, future timestamps and comments older than the configured
limit (maximum 24 hours). GitHub exposes timestamps at second precision:
equal creation and update timestamps do **not** prove an edit never occurred
within that second. The gate verifies the exact current body, but a stolen
operator token could create or alter authorization; retain the records and
protect the account and tokens.

Before any staging writer credentials, a separate `staging-authorization`
job revalidates the evidence, then uses its audit-only role to make one
conditional insert into the consumption table. A duplicate authorization,
table failure or ambiguous response fails closed. Even if no deployment
occurred, a failed/ambiguous consumption or partially completed deployment
requires investigation and a new authorization comment; a rerun cannot reuse
the consumed one. Record the Actions run, attempt, comment IDs and table item.
The writer job rechecks the evidence after consumption.

The table has a string partition key `authorization_id` with value
`<repository-id>:<promotion-comment-id>`, no sort key and no TTL. The job
stores both PR heads/IDs, authorization time, target, manifest hash and run
identity. A reviewed role may call only `dynamodb:PutItem` on this exact table;
the role has no signer, state or deployment access. The workflow supplies
`attribute_not_exists(authorization_id)`; IAM permission for PutItem alone
does not enforce that condition. Restrict assumption to the exact immutable
central workflow, caller repository ID and `staging-authorization` context;
protect the table from deletion, replacement and other writers. Restoring a
table without consumption records can enable replay. Infrastructure owners
must verify retention, backup/recovery, and CloudTrail coverage.

Configure `STAGING_APPROVAL_MODE`, `STAGING_SOLO_AUTHORIZER_ID`,
`STAGING_SOLO_APPROVAL_MAX_AGE_SECONDS`, `STAGING_SOLO_TARGET_ACCOUNT_ID`,
and `STAGING_SOLO_TARGET_REGION` consistently in the relevant `staging`,
`staging-plan`, and `staging-plan-signing` environments. The separate
`staging-authorization` environment needs the solo values, the trusted
producer/target/public-key variables needed to revalidate the applicable
promotion, and `STAGING_SOLO_LEDGER_ROLE_ARN`, `STAGING_SOLO_LEDGER_TABLE_ARN`,
`STAGING_SOLO_LEDGER_ACCOUNT_ID`, and `STAGING_SOLO_LEDGER_REGION`.
No mode, role or table is supplied by a caller input. These are configuration
requirements for a future acceptance exercise, not instructions to enable
trust now.

## Container image promotion

The proposed source image must come from a successful `main` run of the admin-pinned
reusable `container-image.yml`. The evidence binds the source repository,
source commit, run ID and attempt, producer workflow ref, ECR repository, and
SHA-256 image digest. The gate resolves the source commit to exactly one merged
PR targeting `main` and independently verifies its current human approval and
checks; a direct main push is not eligible. The intended flow checks the run,
caller wrapper and Dev SHA tag, then copies the image without rebuilding it.
**Copying is disabled:** the prototype now uses `skopeo copy --all
--preserve-digests`, but no real multi-platform transfer has been verified.

`container-image.yml` can publish without a cache repository. A cache is an
optional performance feature, not a promotion prerequisite; do not provision
an unrequested cache repository just to enable staging. When configured, the
cache repository must be separate from the image repository and the writer
role must be authorized for it. Existing callers with a cache continue to use
the same cache behavior.

The image promotion manifest is exactly:

```json
{
  "schema_version": 1,
  "target": "staging",
  "kind": "image",
  "source": {
    "repository": "OWNER/REPOSITORY",
    "commit": "40-character-main-commit",
    "workflow_run_id": 123,
    "workflow_run_attempt": 1,
    "image_repository": "service",
    "image_digest": "sha256:<64 lowercase hex characters>",
    "producer_workflow_ref": "Celoria-Industries/.github/.github/workflows/container-image.yml@<40-character SHA>"
  },
  "destination": {
    "image_repository": "service"
  }
}
```

`destination.image_repository` must equal the source ECR repository. The
`STAGING_TRUSTED_IMAGE_WORKFLOW` environment variable pins the source reusable
workflow to its full commit SHA.

## Terraform saved-plan promotion

The Terraform producer is
`.github/workflows/staging-terraform-plan.yml`. The infrastructure repository's
main-only caller takes only `source-pr-number` and invokes that central
workflow at an immutable SHA. The source PR must already be merged, currently
human-approved on its exact head, and have successful completed checks. The
producer checks out the PR's **merge commit** and records both that commit and
the reviewed PR head. A source PR approval is not promotion approval.

The prototype has distinct planning, signing, and apply jobs and identities.
The apply job is disabled; the following is the intended design, not proof of
a safe executable-code boundary:

1. The `staging-plan` planner uses the existing bootstrap-account
   `AWS_ROLE_ARN_STAGING_PLAN` state role and the existing member-account
   `AWS_DEPLOY_ROLE_ARN_STAGING_PLAN` profile. It validates both caller
   identities and the configured staging account. These are scoped planning
   permissions, not zero-credential planning.
2. The separate `staging-plan-signing` job downloads the exact plan artifact,
   verifies its GitHub artifact ID/digest and binary SHA-256, and signs a
   canonical typed record with the fixed asymmetric KMS key using the [KMS
   Sign API](https://docs.aws.amazon.com/kms/latest/APIReference/API_Sign.html).
   Its role is limited
   to `kms:Sign` on that immutable key ARN; it has no state, deployment, or
   apply permissions. The private key never leaves KMS.
3. The evidence record binds the source repository, PR number/head/merge
   commit, target account/region, allowlisted layer and its exact root, plan artifact ID/name/digest and binary
   SHA-256, producer workflow ref/run/attempt/head SHA, and immutable KMS key
   ARN. It uses RSA-PSS/SHA-256. The evidence artifact is separate from the
   plan artifact, avoiding an artifact-ID self-reference.
4. A separate reviewed promotion PR binds both artifact IDs and GitHub
   artifact digests plus all target/source/run fields. Before any staging
   apply credentials are requested, the gate checks the artifacts against the
   GitHub API, validates the producer and source PR, verifies the signature
   offline using the independently pinned RSA-3072 public key, and hashes the
   downloaded plan bytes.
5. The disabled apply job is intended to assume the bootstrap state-apply
   role, chain through the member-account deploy-apply profile, and apply the
   verified saved plan. Its current Terraform init/apply can execute providers
   and configuration-defined provisioners with writer credentials; a signed
   plan and read-only lock file do not prevent this.

Terraform promotion manifest schema (exact keys; no additional properties):

```json
{
  "schema_version": 1,
  "target": "staging",
  "kind": "terraform",
  "source": {
    "repository": "Celoria-Industries/infrastructure-as-code",
    "commit": "<source_pull_request_merge_sha>",
    "pull_request_number": 91,
    "pull_request_head_sha": "<40-character SHA>",
    "pull_request_merge_sha": "<40-character SHA>",
    "workflow_run_id": 123,
    "workflow_run_attempt": 1,
    "workflow_run_head_sha": "<40-character main SHA>",
    "producer_workflow_ref": "Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@<40-character SHA>",
    "plan_artifact_id": 456,
    "plan_artifact_name": "staging-terraform-plan",
    "plan_artifact_digest": "sha256:<64 lowercase hex characters>",
    "plan_sha256": "sha256:<64 lowercase hex characters>",
    "evidence_artifact_id": 789,
    "evidence_artifact_name": "staging-terraform-evidence",
    "evidence_artifact_digest": "sha256:<64 lowercase hex characters>",
    "target_account_id": "<12-digit staging account ID>",
    "target_region": "<staging region>",
    "plan_layer": "foundation",
    "terraform_root": "stacks/celoria-industries/staging/foundation",
    "signing_key_arn": "arn:aws:kms:<region>:<bootstrap-account>:key/<immutable-key-id>",
    "signing_algorithm": "RSASSA_PSS_SHA_256"
  }
}
```

Use the generated promotion-manifest source fields in the producer run
summary; do not manually substitute newer artifact IDs, digests, runs, targets,
roots, or keys. The [GitHub Actions artifacts API](https://docs.github.com/rest/actions/artifacts)
provides immutable IDs and SHA-256 digests for private-repository workflow
artifacts on GitHub Team. GitHub artifact attestations are not assumed:
private-repository attestations require a plan that is not available on Team.
Artifact storage is private to repository readers, but Terraform saved plans
can contain sensitive values; review repository access before enabling this
path. See the [artifact attestation plan requirements](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations)
before considering a different provenance design. Artifacts expire after 14 days.
After expiry or a failed/stale producer run, create a fresh plan and a new
reviewed promotion PR.

## Blockers and evidence required

### Terraform execution boundary

The saved plan and KMS signature establish evidence integrity, not the
trustworthiness of executable code or the safety of a change. Terraform
providers are plugins selected by configuration, and Terraform
[`local-exec` provisioners](https://developer.hashicorp.com/terraform/language/provisioners)
can run local commands during apply. Both can run in an environment containing
AWS credentials. The committed provider lock file verifies selected package
checksums, but is itself part of the source under review and
[does not lock remote modules](https://developer.hashicorp.com/terraform/language/files/dependency-lock).
Reviewing the plan alone, matching a signature, or filtering scripts by name
does not establish the requested boundary.

The operator has accepted the ordinary risk of running Terraform in GitHub
Actions; this is not a requirement to prove arbitrary Terraform runs without
credentials. Independently verify least privilege in both accounts. The staging
member-account SCP does not automatically protect the separate bootstrap
account's state role, S3 state and lock objects, or state encryption key.

One possible direction is to explicitly trust a separately reviewed, immutable
Terraform binary and provider set, verify provider package hashes against an
independently controlled allowlist, pin and inspect all transitive modules,
and reject provisioners and other execution or credential-redirection paths
using a complete, tested policy over resolved configuration. Assess backend and
provider configuration, module sources, dynamic provider behavior, and network
access as well. This still trusts those provider binaries and their supply
chain with the scoped credentials: it is **not** proof that arbitrary
untrusted Terraform code cannot access credentials. IAM least privilege and
egress controls reduce impact but are not a complete isolation proof.

An alternative is a separate trusted execution service or credential broker
with a narrowly defined operation interface, so untrusted plan contents never
receive raw writer credentials. That is a larger architectural change and
requires proving that the interface and generated operations express the
needed Terraform behavior; it is not implemented here. Before enabling apply,
verify actual staging and bootstrap-account policies and trust, test credential
scope and target identity, and perform real staging acceptance. No current test
establishes those conditions.

### Image-copy integrity

A candidate implemented in the disabled prototype is
[`skopeo copy --all --preserve-digests`](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md),
which documents copying the full image list and failing if a digest cannot be
preserved. The runner must have `skopeo`; the step fails if it is missing.
The exact command passed a real local transfer between two disposable
OCI-compatible registries, using a pinned multi-platform Alpine index: source
and destination had identical raw index bytes/digest, all 16 descriptors and
all child manifest/config/layer digests verified, across 8 platforms. A
no-credential integration test repeats this in PR CI using disposable
registries. This establishes that Skopeo preserved this fixture between local
registry instances, not compatibility with Amazon ECR. Still test ECR media
types, authentication, missing blobs and conflicting immutable destination
tags against the real accounts and least-privilege policies before rollout.
No AWS/ECR credentials were used in the local test.

## Environment configuration and OIDC

The following variables must be configured for the named GitHub Environment;
GitHub `vars` can also fall back to repository or organization values, so
administrators must review those scopes and the effective values. No role or
target value comes from a workflow caller.

| Environment | Required variables |
|---|---|
| `staging-plan` | `AWS_ROLE_ARN_STAGING_PLAN`, `AWS_DEPLOY_ROLE_ARN_STAGING_PLAN`, `TF_STATE_BUCKET_STAGING`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION` |
| `staging-plan-signing` | `AWS_ROLE_ARN_STAGING_SIGNER`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION`, `STAGING_PLAN_SIGNING_KEY_ARN`, `STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64` |
| `staging` | `AWS_ROLE_ARN_STAGING_APPLY`, `AWS_DEPLOY_ROLE_ARN_STAGING_APPLY`, `TF_STATE_BUCKET_STAGING`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION`, `STAGING_PLAN_SIGNING_KEY_ARN`, `STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64` |

`AWS_REGION` is required: configure it to the bootstrap state bucket's region
for planning and apply, and to the signing key's region for signing. The
signing job rejects a key ARN in a different region. It is not necessarily the
same as the member-account `STAGING_PLAN_TARGET_REGION`.

Set `STAGING_TRUSTED_PLAN_WORKFLOW` in all three environments to the same full
ref:
`Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@<40-character SHA>`.
Set `STAGING_TRUSTED_IMAGE_WORKFLOW` in `staging` to the full pinned
`container-image.yml` ref. The staging image copy additionally needs
`STAGING_PROMOTION_ROLE_ARN`, `STAGING_ECR_REGISTRY`,
`STAGING_AWS_REGION`, `DEV_ECR_REGISTRY`, and `DEV_AWS_REGION`.

The agreed immutable-subject claim order is `repo`, `context`,
`job_workflow_ref`. For example, after a real central workflow SHA is tested,
the infrastructure repository's staging apply subject is:

```text
repo:Celoria-Industries@344492955/infrastructure-as-code@1430164183:environment:staging:job_workflow_ref:Celoria-Industries/.github/.github/workflows/staging-promotion-gate.yml@<GATE_SHA>
```

The full trust subjects are:

```text
repo:Celoria-Industries@344492955/infrastructure-as-code@1430164183:environment:staging-plan:job_workflow_ref:Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@<PLAN_SHA>
repo:Celoria-Industries@344492955/infrastructure-as-code@1430164183:environment:staging-plan-signing:job_workflow_ref:Celoria-Industries/.github/.github/workflows/staging-terraform-plan.yml@<PLAN_SHA>
repo:Celoria-Industries@344492955/infrastructure-as-code@1430164183:environment:staging:job_workflow_ref:Celoria-Industries/.github/.github/workflows/staging-promotion-gate.yml@<GATE_SHA>
```

The plan and signer subjects use the same caller repo identity and producer
workflow ref, separated by environment. Substitute only verified immutable
workflow SHAs; an unset SHA means no staging trust and must fail closed.
Staging writer trust must reject both the legacy bare-environment
subject and old mutable `terraform-operations.yml@refs/heads/main` subject.
Never add a staging branch-only, bare-environment, alias-only key, or wildcard
workflow trust.
The current bootstrap module defaults `staging_plan_producer_sha` to `null`
and `additional_plan_subjects` to empty, so the existing read-only state-plan
role does not trust this producer yet. The infrastructure owner must add only
the exact central reusable plan workflow subject after its immutable SHA is
tested; keep the existing staging write denies and do not create a competing
plan role.

**OIDC migration is repository-wide.** Customizing `include_claim_keys` changes
the subject of every AWS OIDC token in that repository, not only staging. Before
activating it, migrate every affected Dev/global/production credential job to
a compatible immutable reusable workflow and add its exact temporary
non-staging subject to the corresponding role. In particular, a standalone
Dev image workflow does not emit `job_workflow_ref`; leaving it unchanged can
break token issuance before IAM evaluates a trust policy. Keep non-staging
roles working with explicit compatible subjects; do not weaken staging trust
to preserve them. Even after every affected job is verified, keep staging
writers disabled until the blockers above are resolved. Roll back by disabling
staging writer trust first, then coordinating restoration of the prior claim
configuration and compatible non-staging trusts; never restore a staging
legacy or mutable-workflow subject.

## Bootstrap, activation, and key rotation

The infrastructure owner must human-apply the dedicated staging account,
staging roots/state, existing state-plan/state-apply roles, member
deploy-plan/deploy-apply roles, ECR repositories, and their least-privilege
policies. Reuse the existing state-role-to-member-role profile chain; do not
create a competing plan role or make the signer a state/deployment role.

The signing owner must human-apply the bootstrap-account signing stack with an
RSA-3072 asymmetric `SIGN_VERIFY` key (the agreed alias is
`cognitive-staging-plan-signing`), a separate signer role with only `kms:Sign`
on the immutable key ARN, and the exact `staging-plan-signing` OIDC identity.
Export the DER public key returned by KMS ([`GetPublicKey`](https://docs.aws.amazon.com/kms/latest/APIReference/API_GetPublicKey.html))
and pin its base64 value and key ARN
in the environments above. Trust and verification bind the immutable key ARN
and public key, never the alias alone.

Repository-wide OIDC claim customization and role trust changes are human
operations. Confirm every caller repository ID and target account ID from the
owning repository before applying trust. The currently verified infrastructure
repo identity is org ID `344492955`, repo ID `1430164183`; the staging AWS
account ID and final workflow SHAs remain deployment-time values. Keep all
staging writer roles disabled until both blockers above are resolved and the
central workflow revisions are merged, tested, and entered exactly in the
protected environments and role trust.

The verifier currently pins one KMS key/public key at a time. Rotation requires
coordinating the new immutable ARN, public-key variable, signer policy, and
role trust, then producing new signed plans and promotion PRs. Existing
manifests signed by the old key will stop verifying after the pin changes;
do not silently accept aliases or old keys.

## Verification status and operator checklist

The code tests exercise real local RSA-3072/PSS signatures and reject altered
records, plan bytes, signatures, wrong keys, changed targets, stale workflow
refs, and artifact substitution. They do not call KMS, apply infrastructure,
or demonstrate safe Terraform execution or a real multi-platform copy.
Before enabling writers, the owner must verify the public key against KMS,
perform a non-applying KMS sign/verify canary, confirm OIDC claims and role
policies in each environment, verify the existing state/deploy role chain and
S3 state access, confirm repository artifact access/retention, and run a
reviewed staging plan. No live AWS writes, GitHub settings changes, key
retrieval, or infrastructure apply are performed by this shared workflow
change.

## Stop, retry, and rollback

Stop on an unknown account/root, missing configuration, unexpected OIDC
identity, absent protected-main evidence, expired artifact, stale approval,
digest/signature mismatch, or failed/incomplete checks. Save the failed run
link and error message for the owning administrator. Do not substitute a new
artifact in an approved manifest, change the key pin to match unknown evidence,
or add legacy/wildcard IAM trust.

If a setup problem caused the failure, have its owner fix it through the
reviewed setup process and repeat the acceptance checks. For expired or changed
evidence, produce fresh artifacts and obtain a new promotion PR review.
If the producer code changes, review its pinned action dependencies too. After
the fix is merged, update the caller, `STAGING_TRUSTED_PLAN_WORKFLOW`, bootstrap
`staging_plan_producer_sha`, and signer `producer_workflow_sha` to the same
reviewed producer revision through their owning processes. Do not separately
sign a saved-plan artifact from an earlier failed run; run the complete
producer again with a current source authorization. Leave promotion, apply,
and ledger trust disabled during a plan/sign-only repair.
The producer normalizes each upload action's bare 64-character lowercase
SHA-256 output to `sha256:<hash>` immediately after upload, before job outputs,
evidence, or manifests use it. REST artifact digests must match that canonical
value exactly; normalization is not a substitute for the API digest comparison.
The pinned download action extracts a single artifact directly into the
requested destination, so the signer reads `staging-plan-artifacts/tfplan`,
not an extra artifact-name subdirectory.
Rerunning a producer changes its attempt: use the new exact evidence, not an
old approval. For a failed promotion, first inspect which steps executed;
failure after AWS credentials or during Terraform apply may mean partial
changes. Have the infrastructure owner inspect state/resources before deciding
whether the saved plan can be retried or a new reviewed plan is necessary.

Rollback is also a change requiring authorization. Disable staging writer
trust first when the identity/trust configuration is suspect, then follow the
coordinated OIDC rollback below the environment table. This stops future access;
it does not undo resources already changed. Reverting a GitHub commit or
closing a PR does not undo AWS changes or roll back an application. Restore
infrastructure through a new reviewed plan and restore an application through
its deployment runbook and reviewed known-good image. Never delete Terraform
state or signing keys as a shortcut to recovery.
