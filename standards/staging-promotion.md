# Staging promotion

This runbook describes the shared GitHub workflow contract for promoting
reviewed container images and Terraform plans to staging. It defines supported
code behavior, not proof that AWS roles, GitHub environments, branch rules, or
staging resources have been activated. Human-applied bootstrap and real-cloud
acceptance are still required.

## Trust boundary and approval

Promotion is not authorized by a source-code PR, a workflow dispatch, a
successful build, or a successful Terraform plan alone. A **separate PR** must
change only `.github/staging-promotion.json`, target `main`, be merged, have a
current human approval on its exact head from someone other than its author,
and have completed successful checks. The gate rechecks the PR, latest reviews,
checks, source evidence, and manifest immediately before requesting AWS
credentials. Stale approvals, failed/incomplete checks, automation-only
approvals, unexpected files, and mismatched evidence fail closed.
The gate verifies this promotion evidence but does not configure or prove the
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

The central reusable workflow accepts only `promotion-pr-number`. It owns the
fixed copy/apply logic and selects every role and target from protected
environment configuration. It does not expose a caller-controlled role,
command, script, artifact URL, Terraform root, signing key, or AWS operation.
Image callers pass the same single input; they cannot use the gate as an
advisory check and then assume the writer role themselves.

Both caller workflows need only `actions: read`, `checks: read`,
`contents: read`, `pull-requests: read`, and `id-token: write`. The plan
producer uses the runtime artifact service for uploads; it does not request
repository `actions: write`. The reusable jobs narrow permissions further by
job; callers must grant the required permissions because reusable workflows
cannot elevate them.

The staging gate contract is:

| Workflow | Caller inputs | Reusable-workflow outputs |
|---|---|---|
| `staging-promotion-gate.yml` | `promotion-pr-number` (`number`, required) | `promotion-kind`, `source-commit`, `promoted-digest` (image only), `producer-run-id`, `plan-artifact-id`, `evidence-artifact-id` (Terraform only) |
| `staging-terraform-plan.yml` | `source-pr-number` (`number`, required) | `plan-artifact-id`, `plan-artifact-digest`, `evidence-artifact-id`, `evidence-artifact-digest` |

No reusable-workflow input selects a role, signing key, target account,
Terraform root, command, or artifact.

## Container image promotion

The source image must come from a successful `main` run of the admin-pinned
reusable `container-image.yml`. The evidence binds the source repository,
source commit, run ID and attempt, producer workflow ref, ECR repository, and
SHA-256 image digest. The gate resolves the source commit to exactly one merged
PR targeting `main` and independently verifies its current human approval and
checks; a direct main push is not eligible. It also checks the run and caller
wrapper, confirms the Dev SHA tag still resolves to that digest, and copies
that exact manifest to staging. It does not rebuild the image.

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

Planning, signing, and applying use distinct jobs and identities:

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
   commit, target account/region/root, plan artifact ID/name/digest and binary
   SHA-256, producer workflow ref/run/attempt/head SHA, and immutable KMS key
   ARN. It uses RSA-PSS/SHA-256. The evidence artifact is separate from the
   plan artifact, avoiding an artifact-ID self-reference.
4. A separate reviewed promotion PR binds both artifact IDs and GitHub
   artifact digests plus all target/source/run fields. Before any staging
   apply credentials are requested, the gate checks the artifacts against the
   GitHub API, validates the producer and source PR, verifies the signature
   offline using the independently pinned RSA-3072 public key, and hashes the
   downloaded plan bytes.
5. The fixed apply job assumes the existing bootstrap state-apply role, chains
   through the existing member-account deploy-apply profile, checks both
   accounts, checks out the exact reviewed merge commit, runs Terraform init
   with the committed lock file read-only, and applies only the verified saved
   plan. It never regenerates the plan or runs PR-provided helper scripts.

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
    "terraform_root": "stacks/<company>/staging/<root>",
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

## Environment configuration and OIDC

The following variables are read only from the named GitHub Environment; no
role or target value comes from a workflow caller.

| Environment | Required variables |
|---|---|
| `staging-plan` | `AWS_ROLE_ARN_STAGING_PLAN`, `AWS_DEPLOY_ROLE_ARN_STAGING_PLAN`, `TF_STATE_BUCKET_STAGING`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION`, `STAGING_PLAN_ROOT` |
| `staging-plan-signing` | `AWS_ROLE_ARN_STAGING_SIGNER`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION`, `STAGING_PLAN_ROOT`, `STAGING_PLAN_SIGNING_KEY_ARN`, `STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64` |
| `staging` | `AWS_ROLE_ARN_STAGING_APPLY`, `AWS_DEPLOY_ROLE_ARN_STAGING_APPLY`, `TF_STATE_BUCKET_STAGING`, `AWS_REGION`, `STAGING_PLAN_TARGET_ACCOUNT_ID`, `STAGING_PLAN_TARGET_REGION`, `STAGING_PLAN_ROOT`, `STAGING_PLAN_SIGNING_KEY_ARN`, `STAGING_PLAN_SIGNING_PUBLIC_KEY_DER_B64` |

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
to preserve them. Only after every affected job is verified should the human
operator customize the claims and enable staging roles. Roll back by disabling
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
staging writer roles disabled until the central workflow revisions are merged,
tested, and entered exactly in the protected environments and role trust.

The verifier currently pins one KMS key/public key at a time. Rotation requires
coordinating the new immutable ARN, public-key variable, signer policy, and
role trust, then producing new signed plans and promotion PRs. Existing
manifests signed by the old key will stop verifying after the pin changes;
do not silently accept aliases or old keys.

## Verification status and operator checklist

The code tests exercise real local RSA-3072/PSS signatures and reject altered
records, plan bytes, signatures, wrong keys, changed targets, stale workflow
refs, and artifact substitution. They do not call KMS or apply infrastructure.
Before enabling writers, the owner must verify the public key against KMS,
perform a non-applying KMS sign/verify canary, confirm OIDC claims and role
policies in each environment, verify the existing state/deploy role chain and
S3 state access, confirm repository artifact access/retention, and run a
reviewed staging plan. No live AWS writes, GitHub settings changes, key
retrieval, or infrastructure apply are performed by this shared workflow
change.
