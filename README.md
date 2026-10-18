# Celoria Industries — Central `.github` Repository

This repository serves as the organization-wide configuration and workflow hub for **Celoria Industries**. It centralizes continuous integration and delivery (CI/CD) pipelines into reusable GitHub Actions workflows, provides 1-click starter workflow templates for newly provisioned workload repositories, and enforces organizational quality and security standards.

---

## 📑 Table of Contents

- [Overview & Architecture](#overview--architecture)
- [Quickstart: 1-Click Starter Workflow](#quickstart-1-click-starter-workflow)
- [Manual Setup: Caller Workflow Example](#manual-setup-caller-workflow-example)
- [Workflow Specification: `standard-ci.yml`](#workflow-specification-standard-ciyml)
  - [Inputs Reference](#inputs-reference)
  - [Secrets Reference](#secrets-reference)
- [Required GitHub Repository Settings](#required-github-repository-settings)
- [Advanced Usage & Common Scenarios](#advanced-usage--common-scenarios)
  - [FastAPI Backend Service](#1-fastapi-backend-service)
  - [Next.js Web Application](#2-nextjs-web-application)
  - [Monorepo / Subdirectory Workload](#3-monorepo--subdirectory-workload)
- [Security & Governance](#security--governance)

---

## Overview & Architecture

Instead of duplicating hundred-line workflow files across every workload repository, Celoria Industries maintains a **single source of truth** in this repository.

```
Workload Repository (e.g. api-service)
└── .github/workflows/ci.yml
         │
         │ (workflow_call + secrets: inherit)
         ▼
Celoria-Industries/.github
└── .github/workflows/standard-ci.yml
         ├── Detect Environment (Python / Node / Auto)
         ├── Python CI (FastAPI, Ruff linting, Pytest, packaging)
         ├── Node.js CI (Next.js, ESLint, Unit tests, Next build)
         └── Security Scanner (Gitleaks, Trivy vulnerability audits)
```

### Key Benefits
- **Zero Drift**: Security updates, dependency updates, and linting standard improvements apply instantly across all org repositories.
- **Least Privilege**: Default permissions are scoped strictly to `contents: read`.
- **Fast Onboarding**: Developers can initialize a production-ready CI pipeline in seconds.

---

## Quickstart: 1-Click Starter Workflow

When bootstrapping a new workload repository inside the `Celoria-Industries` organization:

1. Navigate to the **Actions** tab in your repository.
2. Click **New workflow**.
3. Under the **Workflows created by Celoria Industries** section, select **Celoria Standard CI/CD**.
4. Click **Configure**.
5. Commit the template to your repository's default branch (`main`).

Your repository will automatically link to the centralized pipeline.

---

## Manual Setup: Caller Workflow Example

To manually add or update the CI pipeline in any existing repository, create `.github/workflows/ci.yml` in your workload repository:

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

permissions:
  contents: read

jobs:
  ci:
    name: Celoria Standard CI
    uses: Celoria-Industries/.github/.github/workflows/standard-ci.yml@main
    with:
      runtime: 'auto'               # 'auto', 'python', or 'node'
      python-version: '3.12'
      node-version: '20'
      working-directory: '.'
      run-lint: true
      run-tests: true
      run-build: true
      run-security-scan: true
    secrets: inherit
```

---

## Workflow Specification: `standard-ci.yml`

**Location:** `.github/workflows/standard-ci.yml`  
**Trigger:** `workflow_call`

### Inputs Reference

| Input | Type | Default | Required | Description |
|---|---|---|---|---|
| `runtime` | `string` | `'auto'` | No | Target runtime environment: `'python'`, `'node'`, or `'auto'` (inspects project manifest files). |
| `python-version` | `string` | `'3.12'` | No | Python version to install and configure via `actions/setup-python@v5`. |
| `node-version` | `string` | `'20'` | No | Node.js version to install and configure via `actions/setup-node@v4`. |
| `working-directory`| `string` | `'.'` | No | Path relative to repository root containing the application codebase. |
| `run-lint` | `boolean` | `true` | No | Whether to run linter checks (Ruff for Python, ESLint / `npm run lint` for Node). |
| `run-tests` | `boolean` | `true` | No | Whether to execute automated tests (Pytest with coverage for Python, Jest/Vitest for Node). |
| `run-build` | `boolean` | `true` | No | Whether to verify application build and packaging (`python -m build` or `npm run build`). |
| `run-security-scan`| `boolean` | `true` | No | Whether to execute secret detection (Gitleaks) and filesystem vulnerability audits (Trivy). |

### Secrets Reference

Workload repositories should invoke the workflow with `secrets: inherit`.

| Secret | Required | Description |
|---|---|---|
| `CODECOV_TOKEN` | Optional | API token for reporting test coverage results to Codecov. |

---

## Required GitHub Repository Settings

> [!IMPORTANT]
> If this `.github` repository is configured as **Private** or **Internal**, GitHub blocks other repositories in the organization from calling its reusable workflows by default.

To enable cross-repository workflow access:

1. In this repository (`Celoria-Industries/.github`), go to **Settings**.
2. In the left navigation, select **Actions** → **General**.
3. Scroll down to the **Access** section.
4. Select **Accessible from repositories in the 'Celoria-Industries' organization** (or select specific workload repositories).
5. Click **Save**.

![GitHub Actions Workflow Access Settings](https://docs.github.com/assets/cb-32598/images/help/settings/actions-access-settings.png)

---

## Advanced Usage & Common Scenarios

### 1. FastAPI Backend Service

For a pure Python FastAPI backend repository:

```yaml
name: Backend CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

permissions:
  contents: read

jobs:
  backend-ci:
    uses: Celoria-Industries/.github/.github/workflows/standard-ci.yml@main
    with:
      runtime: 'python'
      python-version: '3.12'
      run-tests: true
      run-security-scan: true
    secrets: inherit
```

### 2. Next.js Web Application

For a Next.js frontend repository:

```yaml
name: Web Frontend CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

permissions:
  contents: read

jobs:
  frontend-ci:
    uses: Celoria-Industries/.github/.github/workflows/standard-ci.yml@main
    with:
      runtime: 'node'
      node-version: '20'
      run-build: true
    secrets: inherit
```

### 3. Monorepo / Subdirectory Workload

If your service code is situated in a subdirectory (such as `apps/api` or `services/web`):

```yaml
name: Monorepo Service CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

permissions:
  contents: read

jobs:
  service-ci:
    uses: Celoria-Industries/.github/.github/workflows/standard-ci.yml@main
    with:
      working-directory: 'services/api'
      runtime: 'python'
    secrets: inherit
```

---

## Security & Governance

- **Permissions**: Root permissions in `standard-ci.yml` default to `contents: read`. Elevated permissions are declared strictly per-job when required (e.g., `security-events: write` for vulnerability report uploads).
- **Secrets Isolation**: No hardcoded credentials exist in workflows. All sensitive keys are passed dynamically through GitHub Secrets inheritance.
- **Workflow Versioning**: Workload repositories can pin to `@main` for automatic continuous updates, or pin to a tagged release (e.g. `@v1`, `@v1.2.0`) or commit SHA for immutable pipelines.