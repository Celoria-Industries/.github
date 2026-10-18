# Celoria Industries — Central `.github` Repository

This repository serves as the organization-wide configuration and workflow hub for **Celoria Industries**. It centralizes continuous integration and delivery (CI/CD) pipelines into reusable GitHub Actions workflows, provides 1-click starter workflow templates for newly provisioned workload repositories, and enforces organizational quality and native security standards.

---

## 📑 Table of Contents

- [Overview & Architecture](#overview--architecture)
- [Why Node.js / TypeScript Ubiquity?](#why-nodejs--typescript-ubiquity)
- [Native GitHub Advanced Security (GHAS)](#native-github-advanced-security-ghas)
- [Quickstart: 1-Click Starter Workflow](#quickstart-1-click-starter-workflow)
- [Manual Setup: Caller Workflow Example](#manual-setup-caller-workflow-example)
- [Workflow Specification: `standard-ci.yml`](#workflow-specification-standard-ciyml)
  - [Inputs Reference](#inputs-reference)
  - [Secrets Reference](#secrets-reference)
- [Required GitHub Repository Settings](#required-github-repository-settings)
- [Workload Examples](#workload-examples)
  - [1. Fastify Backend Microservice](#1-fastify-backend-microservice)
  - [2. Next.js Web Application](#2-nextjs-web-application)
  - [3. Monorepo / Subdirectory Workload](#3-monorepo--subdirectory-workload)
- [Security & Governance](#security--governance)

---

## Overview & Architecture

Instead of duplicating hundred-line workflow files across every workload repository, Celoria Industries maintains a **single source of truth** in this repository.

```
Workload Repository (e.g. auth-service or web-portal)
└── .github/workflows/ci.yml
         │
         │ (workflow_call + secrets: inherit)
         ▼
Celoria-Industries/.github
└── .github/workflows/standard-ci.yml
         ├── Node CI (Setup, Cache, Linting, Typecheck, Unit Tests, Build)
         ├── GHAS CodeQL SAST Analysis (Deep semantic code security scan)
         └── GHAS Dependency Review & Audit (PR supply-chain vulnerability gate)
```

---

## Why Node.js / TypeScript Ubiquity?

Celoria Industries standardizes on **Node.js + TypeScript** across both backend and frontend systems:
- **Fastify for Backend APIs**: Delivers extreme throughput and low overhead, outperforming traditional synchronous and asynchronous alternatives while providing full schema-based validation (TypeBox / JSON Schema).
- **Next.js for Frontend Web**: Server-side rendering, React Server Components, and seamless modern UX.
- **Language Ubiquity**: DTOs, schemas, utility libraries, and types are shared seamlessly across client and server with zero impedance mismatch.

---

## Native GitHub Advanced Security (GHAS)

We rely strictly on native GitHub features rather than external SaaS security scanners:
1. **GitHub CodeQL SAST**: Performs deep semantic analysis (`javascript-typescript`) using official security queries (`security-extended`, `security-and-quality`). Results publish directly into the repository's **Security → Code scanning alerts** tab.
2. **GitHub Secret Scanning & Push Protection**: Natively prevents credentials and private keys from entering the git commit history.
3. **GitHub Dependency Review**: Evaluates pull requests against the GitHub Advisory Database to block vulnerable dependencies before merge.
4. **Native npm Audit**: Inspects dependencies for known security advisories.

---

## Quickstart: 1-Click Starter Workflow

When bootstrapping a new workload repository inside the `Celoria-Industries` organization:

1. Navigate to the **Actions** tab in your repository.
2. Click **New workflow**.
3. Under the **Workflows created by Celoria Industries** section, select **Celoria Standard CI/CD**.
4. Click **Configure**.
5. Commit the template to your repository's default branch (`main`).

Your repository is now protected by organization-standard CI and GHAS security.

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
      node-version: '20'
      working-directory: '.'
      run-lint: true
      run-typecheck: true
      run-tests: true
      run-build: true
      enable-codeql: true
      enable-dependency-review: true
    secrets: inherit
```

---

## Workflow Specification: `standard-ci.yml`

**Location:** `.github/workflows/standard-ci.yml`  
**Trigger:** `workflow_call`

### Inputs Reference

| Input | Type | Default | Required | Description |
|---|---|---|---|---|
| `node-version` | `string` | `'20'` | No | Node.js LTS version to install and configure via `actions/setup-node@v4`. |
| `working-directory`| `string` | `'.'` | No | Path relative to repository root containing the application codebase. |
| `run-lint` | `boolean` | `true` | No | Whether to execute linter scripts (`npm run lint --if-present`). |
| `run-typecheck` | `boolean` | `true` | No | Whether to execute TypeScript type checking (`tsc --noEmit`). |
| `run-tests` | `boolean` | `true` | No | Whether to execute automated tests (`npm test --if-present`). |
| `run-build` | `boolean` | `true` | No | Whether to verify application build (`npm run build --if-present`). |
| `enable-codeql` | `boolean` | `true` | No | Whether to run GitHub Advanced Security CodeQL SAST analysis. |
| `enable-dependency-review` | `boolean` | `true` | No | Whether to run GHAS Dependency Review on pull requests and npm audit. |

### Secrets Reference

Workload repositories should invoke the workflow with `secrets: inherit`.

| Secret | Required | Description |
|---|---|---|
| `CODECOV_TOKEN` | Optional | API token for reporting test coverage results to Codecov (if applicable). |

---

## Required GitHub Repository Settings

### 1. Enable Reusable Workflow Access (if repository is private)
If this `.github` repository is configured as **Private** or **Internal**, GitHub blocks other repositories in the organization from calling its reusable workflows by default.

1. In this repository (`Celoria-Industries/.github`), navigate to **Settings**.
2. In the left navigation, select **Actions** → **General**.
3. Scroll down to **Access**.
4. Select **Accessible from repositories in the 'Celoria-Industries' organization**.
5. Click **Save**.

### 2. Enable GitHub Advanced Security (GHAS) in Workload Repos
1. In the target workload repository, navigate to **Settings** → **Code security and analysis**.
2. Ensure the following are enabled:
   - **GitHub Advanced Security**
   - **Dependency graph**
   - **Dependabot alerts & Dependabot security updates**
   - **Secret scanning** and **Push protection**
   - **Code scanning** (CodeQL will populate results via SARIF from this workflow)

---

## Workload Examples

### 1. Fastify Backend Microservice

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
      node-version: '20'
      run-typecheck: true
      run-tests: true
      run-build: true
      enable-codeql: true
    secrets: inherit
```

### 2. Next.js Web Application

```yaml
name: Frontend CI

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
      node-version: '20'
      run-lint: true
      run-build: true
      enable-codeql: true
    secrets: inherit
```

### 3. Monorepo / Subdirectory Workload

If your Fastify service or Next.js app is located in a subfolder (e.g., `services/api` or `apps/web`):

```yaml
name: Subdirectory Workload CI

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
      node-version: '20'
    secrets: inherit
```

---

## Security & Governance

- **Permissions**: Root permissions in `standard-ci.yml` default to `contents: read`. Elevated permissions are declared strictly per-job where required (`security-events: write` for CodeQL analysis, `pull-requests: write` for Dependency Review PR comments).
- **Secrets Isolation**: No hardcoded credentials exist in workflows. All sensitive keys are passed dynamically through GitHub Secrets inheritance.
- **Workflow Versioning**: Workload repositories can pin to `@main` for automatic continuous updates, or pin to a tagged release (e.g. `@v1`, `@v1.2.0`) or commit SHA for immutable pipelines.