# Service-to-service authentication

Applies to every Celoria workload that calls another Celoria workload.

## Standard
- **No shared long-lived keys.** No API keys, shared secrets or static tokens
  for service-to-service calls, so there is nothing to rotate or leak.
- **Caller identity is its AWS IAM role**, obtained through EKS Pod Identity
  (one role per ServiceAccount) and used to sign requests with SigV4.
- **The callee authorizes by caller role ARN** from an explicit allowlist in its
  own configuration. Being in the cluster, the VPC or the VPN grants nothing.
- Network policy still applies: authentication is in addition to, not instead of,
  default-deny plus explicit allow rules.

## Out of scope
- Human access to admin endpoints (use SSO/VPN, not a shared key).
- Public, end-user authentication.

## Open: verification mechanism
AWS offers no generic "verify this SigV4 request" API for arbitrary HTTP services.
Options are a callee-side check using a presigned `sts:GetCallerIdentity` request
or fronting with a service that supports IAM authorization. Choose one and ship it
as a shared library before the first service depends on it. Until then, a service
that needs an interim credential must keep it in Secrets Manager (read through
External Secrets), never in the repository or an image, and record the exception here.
