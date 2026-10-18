# Celoria Industries

Welcome to the central organization repository for **Celoria Industries**.

This repository manages organization-wide configurations, standardized developer workflows, reusable GitHub Actions pipelines, and organization profile metadata.

---

## 🏛️ Engineering Philosophy & Golden Paths

At Celoria Industries, our engineering platform follows the **Golden Path** methodology:
- **Full-Stack Language Ubiquity**: All backend services and web frontends are developed in TypeScript on the Node.js runtime, ensuring frictionless code sharing, unified tooling, and maximum performance.
- **Centralized Standards, Decentralized Execution**: Service repositories focus on business logic while standard pipelines enforce quality, type safety, build integrity, and security checks.
- **Native GitHub Advanced Security (GHAS)**: Zero dependency on fragmented third-party security vendors. We leverage first-party GitHub CodeQL SAST, GitHub Secret Scanning, and Dependency Review natively.
- **Least-Privilege by Design**: All workflows enforce strict minimum permissions (`contents: read`).

---

## 🛠️ Technology Ecosystem

| Domain | Primary Stack | Standards & Frameworks |
|---|---|---|
| **Backend Microservices & APIs** | Node.js 20+ / TypeScript | **Fastify**, Zod / TypeBox, ESLint, Vitest |
| **Frontend & Web Applications** | Node.js 20+ / TypeScript | **Next.js**, React, Tailwind CSS |
| **Language & Tooling** | TypeScript 5+ | Single-language ubiquity across client & server |
| **CI/CD Platform** | GitHub Actions | Central Reusable Workflows (`workflow_call`) |
| **Security & Compliance** | GitHub Advanced Security | Native CodeQL SAST, Push Protection, Secret Scanning, Dependency Review |

---

## 🚀 Workload Repositories Quickstart

When creating a new repository under the `Celoria-Industries` organization:

1. Navigate to the **Actions** tab in your repository.
2. Select **New workflow**.
3. Under the **Workflows created by Celoria Industries** section, find **Celoria Standard CI/CD** and click **Configure**.
4. Commit the generated workflow file (`.github/workflows/ci.yml`).

For comprehensive parameter references, monorepo configurations, and local setup, refer to the [Internal Developer Documentation](https://github.com/Celoria-Industries/.github/blob/main/README.md).
