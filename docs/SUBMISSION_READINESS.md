# Submission readiness against supplied challenge

This assessment distinguishes the **legacy generated assessment demo** from the **canonical upload optimizer**. The supplied FAQ asks for a public executable repository, setup steps, prompt-history disclosure and honest limitations. No claim here substitutes for reviewing the final public `main` branch and license/attribution before submission.

| Requirement from supplied problem statement | Present evidence | Status / limit |
|---|---|---|
| End-to-end SKU placement with demand, size, compatibility and picking/replenishment | Legacy `backend/engine.py`; live `backend/live_engine.py` and canonical import | **Implemented with estimates.** Live picking uses graph round trips; replenishment cycles are capacity estimates, not measured travel or labor. |
| Core entities, PK/FK, conditional rules, effective dates | `docs/WAREHOUSE_DATA_CONTRACT.md`, `backend/canonical_schema.py`, `backend/canonical_import.py` | **Implemented for canonical inputs.** Arbitrary vendor CSVs require a mapping adapter. |
| 20 warehouses, 10,000 locations, 5,000 SKUs, 100,000 order lines | `backend/generate.py --scale full` | **Legacy generator supports target.** Full data is generated locally and is not committed. The separate live upload sample has 5,000 order lines, not 100,000. |
| Fast/slow movers, oversize, incompatibility, peak shifts | `backend/generate.py` and legacy fixtures | **Covered in legacy data.** The large live-upload sample is intentionally simple and does not demonstrate every edge case. |
| Five labelled negative scenarios | `data/starter/exception_fixtures.csv`, `data/starter/ground_truth.csv`, legacy validation | **Covered in legacy demo.** Labels are separate from optimizer inputs. |
| Deterministic seed, manifest, reproducibility | Legacy generator seed `20261022`; manifests and `backend/reproducibility.py` | **Implemented for legacy demo.** Large canonical sample generator is deterministic but simpler; its synthetic certificates are not site-approved. |
| Persist events rather than overwrite history | Legacy generated pick/replenishment events and run directories | **Partial.** Live upload analyses are in process memory and disappear on restart; no durable event store. |
| Comparable baseline and optimized results | Both engines use one workload and geometry per comparison | **Modeled comparison.** Not independently measured warehouse savings. |
| Meaningful evaluation / accuracy | Legacy labelled exception checks; live outcome CSV evaluation | **Partial.** Live outcome upload measures adoption agreement and route error where measured. No verified optimal-placement ground truth or held-out production deployment exists, so recommendation accuracy is unknown. |
| CSV/JSON/SQL outputs | Legacy `backend/export.py`; live CSV/XLSX result export | **Available across workflows.** Not every format is available in each workflow. |
| Clear setup and verification | Root `README.md`, backend tests, frontend build | **Documented.** Fresh clone should be retested from public `main` before submission. |
| Public repository, meaningful commits, attribution | Git history and public GitHub repository | **Review before submission.** Verify all intended files are committed and visible on `main`; document external benchmark licenses if redistributed. |
| Prompt history and AI-use disclosure | Root `prompts.md`, linked prominently from README | **Available user prompts recorded.** The file identifies unavailable attachment contents and omitted diagnostics. Assistant responses and earlier unavailable chats are not included. The submitter should explain their personal review and validation. |

## Important limitations to explain to evaluators

- “Any CSV” is not a valid safety claim. WMS-specific columns need explicit mapping, and measured capacity, packaging, policies, inventory and routes cannot be fabricated.
- The canonical sample's `site_test` certificates are synthetic test evidence only. They establish validation behavior, not operational readiness.
- The live engine's large-instance heuristic does not provide a global optimality guarantee. Report solver status and objective gap only when actually available.
- “Placement agreement” is adoption, not accuracy; estimated travel improvement is not observed performance.
- The live app is a local single-user prototype without authentication, durable storage or concurrent-worker coordination.

## Pre-submission checks

1. Clone the public `main` branch into a clean folder and follow the root README exactly.
2. Run the backend tests, frontend build, legacy full generator/validator, and the canonical sample upload.
3. Confirm `sample_data/`, README, source code and documentation are committed to `main`; check for secrets and personal data.
4. Include the actual prompt history and an honest AI-use disclosure with the submission.
