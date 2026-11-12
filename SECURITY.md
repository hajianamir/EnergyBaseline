# Security Policy & Offline Threat Model

Last updated: `2026-11-12`

## 1. Architectural Security Posture

EnergyBaseline is an **offline-first native Windows desktop application** (`PySide6`):

- **Zero inbound network surface:** EnergyBaseline opens **no** HTTP/WebSocket servers, listens on **no** TCP/UDP ports, and embeds **no** browser engine (`QtWebEngine` and web frameworks are banned by `scripts/gates/guard_web_free.py`).
- **Zero telemetry:** No usage data, crash dumps, or plant datasets leave the workstation. Outbound TCP connections occur **only** when a user explicitly configures and tests a read-only external SQL data source.
- **Single environment reader:** Only `energybaseline/config/settings.py` is permitted to read database connection environment variables (`DATABASE_URL`, `POSTGRES_URL`, `DB_CONNECTION_STRING`, `DB_URI`, `EB_DATABASE_URL`), enforced by `scripts/gates/guard_env_readers.py`.

## 2. Data, Credential, and Artifact Controls

| Control Area | Current Baseline (`Phase 0`) | Target Hardening (`Phases 1–9`) |
|---|---|---|
| **Dependency Integrity** | All runtime and CI dependencies are exact-pinned (`==`) and SHA-256-hashed in `requirements.lock`, installed with `pip install --require-hashes`, audited with `pip-audit`, and inventoried in a CycloneDX SBOM (`sbom.json`). | Maintained on every PR and release (`PKG-5`, `PKG-6`). |
| **External SQL Credentials** | Stored via OS credential manager (`keyring` / Windows Credential Manager) with local Fernet fallback; passwords are excluded from application logs (`energybaseline/core/secrets.py`). | Phase 1 (`SCR-1..7`): explicit Windows DPAPI (`CryptProtectData`) primary backend + PBKDF2-HMAC-SHA256 user-passphrase fallback with zero unencrypted key material on disk (`AC-03`). |
| **SQL Query Safety** | Read-only transaction execution + keyword screening (`energybaseline/core/sqlsource.py`). | Phase 2 (`DAT-7`, `AC-06`): `sqlglot` AST parsing permitting exclusively single `SELECT` / `WITH ... SELECT` statements plus driver-level read-only session enforcement and row/timeout caps. |
| **Model Serialization** | Local SQLite storage (`energybaseline/core/store.py`). | Phase 1 / Phase 6 (`SC-10`, `AC-04`): `pickle.loads` eliminated; artifacts persisted via `joblib` alongside SHA-256 manifests verified prior to load. |
| **Executable Signing** | `scripts/sign/sign_artifacts.ps1` verified in CI with ephemeral self-signed certs (`D1=B`). | Production hardware-backed OV/EV Authenticode signing mandatory prior to any distribution outside developer machines (`docs/deviations.md` `DEV-01`). |

## 3. Supported Versions

| Version | Supported |
|---|---|
| `6.0.x` (active v6 engineering branch) | Yes |
| `< 6.0` | No |

## 4. Reporting a Vulnerability

Do **not** open public GitHub issues containing exploit payloads, plant datasets, or credentials.

Report security findings privately to the repository owner (`hajianamir`) via GitHub Private Vulnerability Reporting (Security Advisories) on `hajianamir/EnergyBaseline`, including:

1. Affected commit SHA and OS / Python version.
2. Minimal reproduction steps using synthetic data only.
3. Impact assessment (credential disclosure, arbitrary code execution via crafted project/data file, SQL read-only bypass, or supply-chain CVE).

Target acknowledgement window: **5 business days**. Critical dependency CVEs flagged by `pip-audit` block CI merges immediately until patched or explicitly dispositioned in `docs/deviations.md`.
