# Celoria Industries

Welcome to the central organization repository for **Celoria Industries**.

This repository manages organization-wide configurations, standardized developer workflows, reusable GitHub Actions pipelines, and organization profile metadata.

---

## 🏛️ Engineering Philosophy & Golden Paths

At Celoria Industries, our engineering platform follows the **Golden Path** methodology:
- **Centralized Standards, Decentralized Execution**: Service repositories focus on business logic while standard pipelines enforce security, quality, and packaging checks.
- **Zero-Config Onboarding**: Workload repositories bootstrap continuous integration using 1-click organization workflow templates.
- **Security by Default**: All workflows adhere to the principle of least privilege (`contents: read`), automated static vulnerability analysis, and secret leakage prevention.

---

## 🛠️ Technology Ecosystem

| Domain | Primary Stack | Standards & Frameworks |
|---|---|---|
| **Backend Services** | Python 3.12+ | FastAPI, Pydantic, Ruff, Pytest |
| **Frontend & Web** | Node.js 20+ | Next.js, React, TypeScript, ESLint |
| **CI/CD Platform** | GitHub Actions | Central Reusable Workflows (`workflow_call`) |
| **Security & Compliance** | Trivy, Gitleaks | Automated secret scanning, SAST, dependency auditing |

---

## 🚀 Workload Repositories Quickstart

When creating a new repository under the `Celoria-Industries` organization:

1. Navigate to the **Actions** tab in your repository.
2. Select **New workflow**.
3. Under the **Workflows created by Celoria Industries** section, find **Celoria Standard CI/CD** and click **Configure**.
4. Commit the generated workflow file (`.github/workflows/ci.yml`).

For comprehensive parameter references, custom runtime overrides, and local setup, refer to the [Internal Developer Documentation](https://github.com/Celoria-Industries/.github/blob/main/README.md).
