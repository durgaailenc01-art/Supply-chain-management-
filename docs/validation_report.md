# Validation and simulation report

Generated from seed `20261022`. The source is wholly synthetic. CSV manifests record per-table row counts and SHA-256 digests. This report describes command outputs from the local run; regenerate to verify.

| Check | Starter | Full |
| --- | ---: | ---: |
| Warehouses | 2 | 20 |
| Locations | 200 | 10,000 |
| SKUs | 100 | 5,000 |
| Order lines | 2,000 | 100,000 |
| Integrity errors | 0 | 0 |
| Exception labels detected correctly | 5/5 | 5/5 |
| Two-run checksum comparison | Pass | Pass |

| Simulated KPI | Starter baseline | Starter proposed | Full baseline | Full proposed |
| --- | ---: | ---: | ---: | ---: |
| Pick distance (m) | 70,340 | 39,976 | 7,756,672 | 3,430,884 |
| Pick time (h) | 24.62 | 17.59 | 2,212.19 | 1,210.85 |
| Throughput (lines/h) | 81.25 | 113.72 | 45.20 | 82.59 |
| Replenishment tasks | 114 | 111 | 6,079 | 6,623 |
| Space utilization proxy (%) | 23.69 | 24.03 | 22.45 | 21.19 |

Distance falls 43.17% in starter and 55.77% in full. At full scale, the nearest-slot rule increases replenishment by 544 tasks and reduces estimated space utilization by 1.26 percentage points. This tradeoff is visible rather than hidden; a later multi-objective score can balance refill workload against pick distance. These are model outputs based on synthetic demand, simplified geometry and a stock proxy. They are not measured warehouse savings.

Validation checks PKs, FKs, allowed values, date order, slot exclusivity, physical compatibility, event distance, replenishment arithmetic, source checksums, and all five exception labels. `python -m unittest discover -s backend/tests -v` passed 7 core, API-handler, filter, and forecast tests. JSON and SQLite seed export completed with zero foreign-key errors for starter data.

The React production build passed. The optional scikit-learn forecast also ran on starter data: for the top 10 SKUs, chronological 14-day holdout MAE was 0.473 picks/day versus 1.273 for a seven-day rolling mean. This single synthetic split is evidence of implementation, not a generalization claim.

The FastAPI app imported and its route handlers passed in-process tests. A local Uvicorn run also returned HTTP 200 for overview, recommendations, exceptions, validation, demand forecast, optimization POST, and CSV export. The React production build passed. Vite served the page and proxied the warehouse overview and filtered recommendations to FastAPI. Browser checks confirmed live warehouse filtering, SKU search, an empty result placeholder, recommendation detail, forecast results, and a new optimization run refreshing the displayed run ID.
