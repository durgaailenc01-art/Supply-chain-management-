# Slotwise enterprise readiness audit

Audit date: 2026-11-01. Scope: the existing backend, React frontend, generated starter and full CSV datasets and latest run outputs, documentation, tests, plus the user-supplied CSV/JSON files previously profiled from `C:/Users/DurgaPrasad/Download/supply2` and `C:/Users/DurgaPrasad/Desktop/supply_chain_data.csv`. This was a read-only application/data review; no application code, source data or optimization run was changed. Evidence and results below distinguish deterministic internal consistency from external operational accuracy.

## Executive assessment

Slotwise currently works as an **explainable, single-user simulation demo**. It generates synthetic warehouses and demand, produces one compatible pick location per SKU, stores immutable proposal runs, compares the proposals with a seeded random compatible baseline, and presents the calculations through a functioning API and dashboard. The starter and full datasets pass the implemented validator. The latest proposals also pass a separate read-only check for unique SKU/location assignments and `fits()` compatibility.

It is **not ready to recommend physical moves in a real warehouse**. Its source data is synthetic; its distance and time are formula outputs rather than measured routes or pick durations; it has no actual inventory or location occupancy; and its refill and capacity model can substantially misstate pick-face capacity. The algorithm optimizes proximity greedily and does not minimize a complete operational cost or guarantee globally optimal/fully assigned results. The optional forecast is separate and does not influence slotting.

## 1. What is implemented and genuinely working

| Component | Current behavior | Evidence |
| --- | --- | --- |
| Generator | `backend/generate.py` creates deterministic starter (2 warehouses, 200 locations, 100 SKUs, 500 orders, 2,000 lines) and full (20, 10,000, 5,000, 25,000, 100,000) CSV snapshots with seed `20261022`, manifest counts and SHA-256 checksums. | `generate()`, `data/*/manifest.json` |
| Assignment | `backend/engine.py` filters a SKU's candidate locations with `fits()`, reserves one location per SKU, and saves a proposal, explanations and metrics under a run ID. | `fits()`, `assign()`, `optimize_dataset()` |
| Comparison | Baseline and proposal use the same generated order lines, SKUs and locations. API warehouse selection recomputes scoped KPIs. | `metrics()`, `backend/app.py:overview()` |
| Validation | Generated tables pass implemented primary/foreign-key, enum, basic date, baseline assignment, arithmetic, checksum and five fixture-label checks. | `backend/validate.py`; read-only validation executed for both scales: zero reported errors and 5/5 fixture labels. |
| APIs and UI | FastAPI serves overview, warehouse list, searchable/sortable/paginated recommendations, exceptions, validation, forecast, optimization trigger and allowlisted baseline CSV exports. React fetches real API responses, filters by warehouse and shows loading/error/empty states. | `backend/app.py`, `frontend/src/api.ts`, `frontend/src/Dashboard.tsx` |
| Forecast | A scikit-learn random forest trains on top-SKU daily synthetic order-line counts and displays 14-day holdout MAE against a seven-day average and a seven-day recursive forecast. | `backend/demand_forecast.py` |
| Reproducibility/export | Generator checksums are repeatable; baseline tables can be exported to JSON and SQLite-compatible SQL. | `backend/reproducibility.py`, `backend/export.py` |

Independent read-only inspection of the latest saved proposals found **100/100** starter and **5,000/5,000** full SKUs assigned, no duplicated proposed slots, no proposal failing the current `fits()` predicate, exact agreement between saved and recalculated optimized metrics, and matching input-manifest checksums. This verifies the current code and data agree with each other; it does not verify that the rules describe a real facility.

## 2. End-to-end calculation trace

1. **Input generation.** Four synthetic zones per warehouse are created. Locations receive fixed dimensions/load limits and integer `aisle`/`bay` values. SKU size, temperature and hazard are generated. Orders cycle through warehouses and a 90-day calendar. Each order has four random weighted SKU lines. All generated orders are `fulfilled` (`backend/generate.py`).
2. **Baseline.** `assign(..., baseline=True)` puts length-over-70-cm SKUs first, then chooses a compatible available location with a fixed random generator. It is **not an observed current WMS layout**. `pick_events.csv` and `replenishments.csv` are later derived from this baseline and the same formulas; they are not empirical labels (`backend/generate.py`, `backend/engine.py`).
3. **Demand velocity.** `workload()` adds **one pick per order-line row** and adds the row's `quantity` to ordered units. There is no status, recency, warehouse-specific time window, service-level or forecast weighting. In generated data all orders are fulfilled, so the lack of status filtering is hidden (`backend/engine.py:workload`).
4. **Eligibility.** `fits()` requires same warehouse, active/unblocked location, exact temperature and hazard match, each SKU dimension no larger than the corresponding bin dimension, single-unit weight within `max_weight_kg`, and single-unit volume within `capacity_cm3`. Orientation, total load, existing stock, rack/zone status and effective dates are absent (`backend/engine.py:fits`).
5. **Proposed assignment.** Oversized SKUs (length >70 cm) are processed first. All others are ordered by descending historical pick-line count, then SKU ID. For each SKU the nearest currently unreserved eligible location is taken; ties favor larger *volume-derived* unit capacity, then location ID. This is a deterministic greedy heuristic, not a global assignment optimum. The displayed `score` is calculated **after** selection and is not the optimization objective (`backend/engine.py:assign`).
6. **Distance/time.** A location's per-line route is `2 × (aisle × 4 + bay × 2)` metres. Total travel is the sum of `pick_count[SKU] × route_metres[assigned_location]`. Estimated seconds are `total_metres / 1.2 + order_line_count × 15`; throughput is lines divided by those estimated hours. Routes are independent round trips, with no aisle graph, batch/wave route, height, congestion or measured handling time (`backend/engine.py:distance`, `metrics`).
7. **Replenishment/space.** Pick-face unit capacity is `floor(bin_volume / unit_volume)` and never exceeds fewer than one unit. Refill tasks are `max(0, ceil(total_ordered_units / unit_capacity) − 1)`. Space utilization is the volume of `min(ordered_units, unit_capacity)` per assigned SKU divided by all active/unblocked bin volume. These are proxies, not actual stock or labor events (`backend/engine.py:unit_capacity`, `metrics`).
8. **Output.** Each run writes `recommendations.csv`, proposed `slot_assignments.csv`, and `metrics.json`; `latest_run.json` points to the newest run. The UI compares that run to the baseline and lets the user inspect each SKU. No stock is moved by the software (`backend/engine.py:optimize_dataset`, `frontend/src/Dashboard.tsx`).

## 3. Material accuracy and safety gaps

### P0 — must resolve before operational recommendations

**Capacity is checked for one unit, not the proposed stock quantity.** `fits()` compares one SKU unit's weight to the bin's load limit. `unit_capacity()` and refill counts use volume only. In the saved starter proposal, **72 of 100** selected SKU/bin pairs have `volume_based_unit_capacity × unit_weight > bin_max_weight`; in the full proposal, **4,067 of 5,000** do. These are *potential full-pick-face overloads under the model's own capacity assumption*, not evidence that actual shelves are overloaded, because actual inventory is unavailable. If capacity is capped by both volume and total weight, the same ordered units imply **449 rather than 111** proposed refill tasks in starter and **21,421 rather than 6,623** in full. Even the weight-capped figures remain crude because packing, case orientation and restock policy are unknown. The current replenishment KPI is not reliable for production.

**No physical route or time calibration.** The aisle/bay expression is an artificial distance proxy; the dataset has no aisle connectivity, cross-aisles, one-way paths, start/finish stations, bin coordinates or observed task durations. `pick_events.distance_m` is generated from that same expression, so validation of it is circular. The displayed travel, time, throughput and savings are not externally validated.

**No real current state.** The baseline is randomly assigned, albeit compatible under current rules. There is no inventory by location, available quantity, reserve stock, occupied capacity, current multi-location SKU placement, current WMS assignment, relocation effort or stock movement workflow. A proposal can be physically impractical even if `fits()` returns true. Current-vs-proposed percentage improvements are against a synthetic random baseline, not a customer's actual layout.

**Incomplete eligibility rules.** `fits()` ignores zone and warehouse active status, SKU active status, profile effective dates, total stored weight, orientation/packing, stacking, ergonomics, fire/chemical segregation beyond exact label equality, shelf reach and equipment limits. It treats a single SKU per bin as universal. Conversely, a real multi-SKU or multi-pick-face facility cannot be represented. The generated data has all zones, warehouses and SKUs active, so those cases are not exercised.

**No feasibility/coverage gate.** The greedy algorithm can reserve the only compatible slot for a flexible high-demand SKU before a restricted SKU is processed. A feasible global matching can therefore exist while the heuristic leaves a SKU unassigned. If any SKU is unassigned, `metrics()` excludes its travel but still counts all order lines in `pick_lines` and handling time; reported travel can fall and throughput rise spuriously. A production run must fail or explicitly penalize uncovered demand.

### P1 — model quality and validation gaps

**Proximity dominates the objective.** The algorithm does not use replenishment workload, travel of replenishment tasks, space utilization, congestion, demand uncertainty, move cost or labor capacity to choose a slot. Full synthetic data illustrates the tradeoff: pick distance falls **7,756,672 → 3,430,884 m**, while reported refill tasks rise **6,079 → 6,623** and the space proxy falls **22.45% → 21.19%**. The result is not a verified net operational saving.

**The data generator contains artificial temporal structure.** Warehouse assignment is `order_index % warehouse_count` and day is `order_index % 90`. Consequently each starter warehouse has orders on only **45 of 90** days, and each full-scale warehouse on only **9 of 90** days. All generated pick events share one timestamp, and all generated replenishment rows share one timestamp. The latter are per-SKU aggregate counts, not a chronological event stream. Forecast and labor backtests on these records would learn artefacts of the generator.

**Forecast validation does not represent its displayed future use.** The 14-day holdout features use observed lagged counts from within the holdout period, giving a series of one-step predictions; the displayed next-seven-day forecast recursively feeds its own predictions. Top SKUs and their ranks are selected from **all** order lines, including the holdout period, which leaks future selection information. There is one split, no warehouse segmentation, no uncertainty/calibration, no status filtering, and no seasonal coverage. Forecast MAE on this synthetic split cannot establish production forecast accuracy. `forecast()` is never called by `assign()` or `optimize_dataset()`, so the optional ML result has **no effect** on slot recommendations (`backend/demand_forecast.py`).

**Validation is mainly self-consistency.** `validate_dataset()` validates the generated baseline and generated pick/refill arithmetic; it does not validate run proposal files. It compares five fixed, deliberately created exception fixtures with their authored labels; 5/5 is not a real incident detection rate. Manifest hashes show file integrity relative to the manifest, not factual correctness. The independent read-only proposal check above helps for this snapshot but is not an enforced API gate. Some checks described in `docs/data_dictionary.md` (for example strictly positive dimensions and all lifecycle transitions) are not implemented. `docs/business_rules.md` mentions frozen temperature even though current enums/code only allow ambient and chilled.

### P2 — service and product readiness gaps

The API is a local demo with no authentication, authorization, tenant boundary, role approval, rate limits or audit trail. `GET /api/overview` can create a run through `current_run()`, and `POST /api/optimize` writes shared latest-run state without a lock or queue. It does not validate inputs before optimizing. Repeated GETs read whole CSV files; forecast trains synchronously during a request. An invalid or missing latest-run pointer can fail the request. `/api/export/{table}` serves only baseline tables, so proposed assignments have no reviewed export path. `SLOTTING_DATA_DIR` is a compatible-schema directory selector, **not a Kaggle/WMS importer** (`backend/app.py`).

The React UI is appropriately transparent about synthetic estimates, exposes filters and review details, and uses API-backed figures. It still offers no data-import, scenario configuration, move approval, feasibility certificate, audit history or measured-vs-predicted view. Its fixed text always calls the source synthetic, which is accurate today but would need source-aware presentation for a real tenant. It can show independent API responses from different run revisions during refresh because overview/recommendation/validation calls are separate (`frontend/src/Dashboard.tsx`, `frontend/src/api.ts`).

Existing tests cover seven generated-data/API cases and basic UI build behavior was previously documented. The API tests directly call handlers and one test creates a new run; they do not cover concurrent HTTP use, malformed uploads, security, real WMS import, optimizer infeasibility, proposed-run validation, route calibration or measured outcomes. Tests were **not rerun in this audit** because the optimization test writes a run and the user requested no dataset changes (`backend/tests`).

## 4. Dataset and relationship audit

### Current application dataset

The API reads **`data/starter` by default**; `data/full` exists locally and is used only if `SLOTTING_DATA_DIR` is set before startup. Both manifests explicitly say `fully synthetic; no external data`. The 12 tables are:

| Tables | Valid relationships/use | Missing real-world information |
| --- | --- | --- |
| `warehouses`, `zones`, `locations` | Warehouse → zone → bin IDs resolve; bin and zone warehouse IDs agree. Starter has 2/8/200 rows, full 20/80/10,000. | Surveyed coordinates, aisle graph, rack-level capacities, actual status/closures and asset history. |
| `skus`, `sku_profiles` | Each of 100 starter or 5,000 full SKUs has one current profile and matching warehouse ID. | Measured unit/case/pallet attributes, packing orientation, expiry, stackability, real storage rules and multi-warehouse SKU inventory. |
| `orders`, `order_lines` | Order-line → order and SKU references resolve and order/SKU warehouses agree; 500/2,000 starter and 25,000/100,000 full. | Real order lifecycle, allocations, cancellations, pick waves and actual picked quantities. |
| `slot_assignments` | Baseline uses one compatible active bin per SKU in current generated data. | Actual current/previous assignments, quantities, bin sharing, inventory reservations and relocation state. |
| `pick_events`, `replenishments` | One generated pick row per order line; one generated aggregate refill row per SKU. Values reconcile with the generator's formulas. | Independent scanned pick timestamps/routes, measured walking/handling and real replenishment events/labor. |
| `exception_fixtures`, `ground_truth` | Five known labelled cases are intentionally separated from clean optimizer input. | Real exception stream and independently observed incident labels. |

Starter/full validator results: `valid=true`, zero errors, 5/5 fixed fixture labels. This establishes that the generated snapshots satisfy the implemented schema and arithmetic. It does **not** establish operational accuracy.

### Additional user-supplied files (not used by Slotwise)

The repository has no adapter that loads these files. Their usefulness is limited to a separate, carefully mapped demand prototype:

| File | Observed profile and usable fields | Blocking issue for slotting |
| --- | --- | --- |
| `brand.csv` | 14 brand records; ID/name/type for grouping. | No operational information. |
| `customer.csv` | 3,760 rows; customer/channel context. | 3,740 distinct IDs; 20 IDs each identify different records. Customer personal fields should not be imported when aggregate demand suffices. |
| `product.csv` | 540 products; product ID, category, brand, stock/active flags, vendor and prices. | No measured dimensions, unit weight, temperature/hazard needs, stocking rules or inventory by bin. `unit_size` is a consumer pack label. |
| `sales_order.csv` | 10,000 orders over 2025-07-01–2025-09-30; date, status, customer, channel. | Orders are not pick events; only 92 days; 786 orders predate the first linked customer's opening date. |
| `sales_order_details.csv` | 47,156 lines, 537 products; product ID and ordered units/line frequency. All line → order and product IDs resolve. | Need status/fulfillment policy. Only 32,272 lines refer to products marked stock, active and not discontinued; even these are unverified as physical picks. |
| `vendor.csv` | 11 vendors; nonblank product vendor references resolve. | No actual purchase-order receipts or replenishment service level. The 320 blank product vendor IDs are private-label products. |
| `docs_category_weighting.csv` | Eight category/channel weights. | Describes sample-data rules, not observed picks. |
| `docs_customer_mix.csv` | Two customer-mix definitions. | Context, not warehouse evidence. |
| `docs_discount_logic.csv` | Four discount rules. | Not relevant to slotting. |
| `docs_tax_logic.csv` | Three tax rules. | Not relevant to slotting. |
| `sales_channel_lookup.csv` | Four channel mappings. | Can normalize demand segments, but no warehouse/bin mapping. |
| `the_kings_pantry_powerbi_theme.json` | Presentation theme. | No model fields. |
| `supply_chain_data.csv` | 100 SKUs, product type, stock/lead-time and supplier/shipping fields. | IDs such as `SKU0` do not join the `product.csv` IDs; `Location` is one of five cities, not a bin. No dated transactions or pick routes. Two lead-time columns disagree in 99/100 rows and require semantic definitions. Treat as separate exploratory data. |

The three-month sales files can estimate **ordered** SKU frequency after data-quality rules are agreed. They cannot be merged directly with the synthetic Slotwise location data to claim real recommendations, and no provided file links a real product to a real warehouse bin. Their provenance as operational versus illustrative data is unconfirmed. Full external-file profiling is also recorded in `docs/enterprise_data_readiness.md`.

## 5. Recommended improvement order and acceptance gates

1. **Define one real facility's data contract and acquire source exports.** Require WMS bin master/layout (surveyed aisle graph and coordinates), SKU/unit/case physical attributes, inventory by bin, current and historical assignments, order allocations, scanned pick events, replenishments and task timestamps. Define source ownership, units, effective times and refresh cadence. Quarantine incomplete records. **Gate:** coverage and lineage are reported; missing required physical fields block affected recommendations.
2. **Make physical feasibility complete and independently checked.** Model capacity as inventory quantity × unit weight/volume with packing/orientation rules, bin occupancy, multiple pick faces, zone/equipment/safety policies and temporary closures. Validate **every proposed run** before exposing it. **Gate:** zero hard-rule violations and zero unaccounted demand; infeasibility is explicit rather than rewarded by KPIs.
3. **Calibrate a warehouse digital twin.** Replace the aisle/bay expression with shortest walkable-path distances and actual pick/batch routing. Fit handling and replenishment time from recorded tasks. Replay historical operations on the historical layout before estimating changes. **Gate:** pre-agreed held-out route/time/refill errors by facility and workload segment; no percentage savings claim until measured calibration passes.
4. **Optimize total operational value.** Compare the current heuristic with constrained assignment/flow or mixed-integer/CP-SAT approaches on tractable scopes. Include pick travel, refill effort, move cost, space/service and robust demand while enforcing hard rules. Record solver status, time limit and optimality gap or heuristic quality. **Gate:** feasible proposals improve a pre-agreed multi-objective score against the *actual* current layout on time-separated backtests.
5. **Rebuild demand forecasting as an optional, validated input.** Use actual picks or fulfilled allocations, warehouse/sku segmentation, rolling-origin backtests, no future-based SKU selection, direct multi-step evaluation, intermittent-demand baselines and uncertainty. Publish metric, horizon, coverage and cohort rather than a blanket "90–95% accurate" claim. **Gate:** forecast materially beats simple baselines on real held-out periods and improves downstream slotting decisions.
6. **Pilot and productionize.** Run shadow mode, then staff-approved limited moves with measured before/after outcomes and rollback. Add transactional storage, tenant isolation, RBAC, approval/audit logs, background jobs, idempotent WMS integration, input/model/run versioning, observability and tests for concurrency/security/failure recovery. **Gate:** measured pilot benefit without safety, service or refill regressions under the approved policy.

## Audit verification notes

- Read-only `python -m backend.validate --dataset data/starter` and `data/full`: both passed with zero errors and 5/5 fixtures.
- Read-only latest-run inspection: starter and full proposed assignment uniqueness, `fits()` compatibility, manifest checksum and metrics recomputation all passed. This is narrower than full operational validation.
- Read-only capacity sensitivity: recomputed maximum theoretical pick-face units as `min(floor(bin_volume/unit_volume), floor(bin_max_weight/unit_weight))`; refill totals above follow the existing ordered-unit formula with only that capacity change. No physical packing or real inventory assumption was added.
- No optimizer, generator, forecast, mutable API tests or frontend build was executed during this audit. No application or dataset files were changed.
