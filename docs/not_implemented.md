# Explicitly Unimplemented & Optional Scope (`[O]` / Deferred)

Last updated: `2026-11-12` (Phase 0 — Truth & Rails)

Per governance rule `G-4` (`docs/EXECUTION_PLAN.md`), EnergyBaseline never exposes dead UI controls, silent no-ops, or fake stubs for unimplemented features. Items below are either `[O]` (Optional) or explicitly scheduled/deferred with transparent runtime status messages.

## 1. Optional (`[O]`) Items Not Scheduled for v6.0 Default Build

| ID | Feature | Priority | Status | Behavior in Application |
|---|---|---|---|---|
| `LIF-4` | System tray background minimize / notification icon | `O` | Not implemented | Closing the main window exits cleanly via normal desktop lifecycle; no dead tray toggle is shown in Preferences. |
| `SHL-8` | Secondary light/dark runtime theme switcher | `O` | Not implemented | Application ships a single cohesive QSS desktop stylesheet (`energybaseline/gui/style.py`) verified for WCAG AA contrast in Phase 8. |
| `F9-Prophet` | Meta `prophet` additive/multiplicative seasonal model | `O` | Optional extra | Not bundled in `requirements.lock` (avoids CmdStan C++ toolchain requirement on offline Windows machines). Recorded explicitly in the Model Evaluation Register as `skipped` / `not installed` with plain-English reason. |

## 2. Ratified Deferred Item (`[S]`)

| ID | Feature | Priority | Status | Re-entry Trigger |
|---|---|---|---|---|
| `PKG-3` | Production CA-issued OV/EV Authenticode certificate signature | `S` (per `D1=B`) | Deferred for internal dev builds | Mandatory before **any** distribution of `EnergyBaseline.exe` outside developer machines (`docs/deviations.md` `DEV-01`). Signing pipeline (`scripts/sign/sign_artifacts.ps1`) is implemented and self-tested in CI. |

## 3. Lite Build Profile (`D3 = A`)

When built with `python scripts/build_exe.py --profile lite` (or installed in a Python environment without `torch`), `F6` (`LSTM`) is not executed; `energybaseline/core/models.py` records status `not evaluated` with reason `PyTorch not installed — skipped cleanly` in the Model Evaluation Register while `MLP` (`scikit-learn`) and all other families continue normally.
