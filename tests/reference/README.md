# R Reference Validation Format (D4 = B)

This directory holds deterministic R reference scripts and versioned JSON fixtures used to validate
custom statistical tests introduced in **Phase 4** (Chow, Quandt–Andrews, Pettitt, Diebold–Mariano / HLN,
Nemenyi, Little's MCAR via `MissMech::mcartest`, and Model Confidence Set).

> **Phase 0 status:** skeleton and specification only. Statistical tests and `.R`/`.json` pairs land in Phase 4.

## File pairing convention

For each custom statistical test `<test_slug>`:

- `tests/reference/<test_slug>.R` — standalone, deterministic R script accepting fixed synthetic inputs and emitting `<test_slug>.json`.
- `tests/reference/<test_slug>.json` — versioned reference output committed alongside `<test_slug>.R`.

## Canonical JSON schema

Every `<test_slug>.json` file must conform to the following top-level object structure:

```json
{
  "schema_version": "1.0",
  "test_id": "T5_chow",
  "test_slug": "chow",
  "R_version": "4.4.2",
  "packages": {
    "strucchange": "1.5-4"
  },
  "seed": 2024,
  "n": 200,
  "date": "2026-11-12T00:00:00Z",
  "r_script_sha256": "<64-hex lowercase SHA-256 of tests/reference/<test_slug>.R with LF line endings>",
  "tolerances": {
    "statistic_rtol": 1e-6,
    "statistic_atol": 1e-8,
    "p_value_atol": 1e-4
  },
  "cases": [
    {
      "case_id": "planted_break_t100",
      "inputs_sha256": "<64-hex lowercase SHA-256 of canonical input vector>",
      "values": {
        "statistic": 42.183921,
        "p_raw": 0.000012,
        "df": [2, 196]
      }
    }
  ]
}
```

## Required fields

| Field | Type | Requirement |
|---|---|---|
| `schema_version` | `string` | `"1.0"` |
| `test_id` | `string` | Catalog ID (`T1`..`T14` / sub-test identifier) |
| `test_slug` | `string` | Stem matching `<test_slug>.R` and `<test_slug>.json` |
| `R_version` | `string` | Exact R runtime version used to generate the fixture |
| `packages` | `object` | Map of R package name → exact installed version string |
| `seed` | `integer` | Deterministic RNG seed |
| `n` | `integer` | Sample size of primary reference case |
| `date` | `string` | ISO-8601 UTC generation timestamp |
| `r_script_sha256` | `string` | SHA-256 digest of the corresponding `.R` file |
| `tolerances` | `object` | Explicit `rtol` / `atol` bounds verified in Python tests |
| `cases` / `values` | `array` / `object` | Expected numerical outputs compared against `energybaseline` |

## Anti-staleness and CI guards (ratified D4 = B)

1. **PR staleness gate (Phase 4):** `tests/test_reference_staleness.py` recomputes the SHA-256 of every `<test_slug>.R` and fails the build immediately if `r_script_sha256` in `<test_slug>.json` does not match.
2. **Nightly R verification (`.github/workflows/r-reference.yml`):** runs on `schedule` (nightly) and `workflow_dispatch`. In Phase 4 it executes every `.R` script in clean R, compares fresh outputs against committed `.json` within `tolerances`, and opens a GitHub issue automatically on divergence.
3. **Freshness expiry:** if the nightly R workflow has not succeeded within 14 days, reference evidence is considered expired and Gate G4 cannot close.
