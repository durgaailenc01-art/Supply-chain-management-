# Enterprise slotting: data readiness and validation plan

Audit date: 2026-11-01. This is a read-only profile of the user-provided CSV/JSON files; source records were not copied into the repository. Public benchmark descriptions were checked at their source sites. The audit does not establish that a Kaggle download is actual operational data or that it may be used commercially.

## Decision

The current app remains a synthetic decision-support demo. The supplied sales files can support a **prototype demand signal**, after cleaning and defining which orders represent warehouse picks. None of the supplied files contains the physical location, inventory, route, or actual pick-event data needed to validate real slot recommendations or measured savings. Do not relabel the current KPI estimates as real-world accuracy.

## Supplied-file audit

| File | Profile | Use for this product | Limitation |
| --- | --- | --- | --- |
| `brand.csv` | 14 brands | Optional product grouping | No warehouse operations. |
| `customer.csv` | 3,760 rows, 3,740 distinct customer IDs | Optional channel/customer segmentation, with minimised personal data | 20 IDs occur twice with different customer records; 786 orders predate the first linked customer's `date_opened`. Resolve identity before using joins. |
| `product.csv` | 540 products, 537 seen in sales lines | Product catalogue, category, active/stock flags, price/cost context | No dimensions, weight, cases/pallets, temperature, hazard, stackability, or warehouse-specific stock. `unit_size` such as `16 oz` is a retail package label, not a measured slot footprint. |
| `sales_order.csv` | 10,000 orders over 2025-07-01 through 2025-09-30 (92 days) | Dated order demand, channel and status | Orders are not actual completed pick events. Three months is insufficient for annual seasonality or robust production validation. |
| `sales_order_details.csv` | 47,156 lines across 537 products | SKU order-line frequency and ordered units | Need policy for open/shipped/closed orders and non-stock/inactive/discontinued items; ordered quantity is not necessarily picked quantity. |
| `vendor.csv` | 11 vendors | Optional replenishment planning | No received purchase orders, service levels or real replenishment events. 320 blank product vendor IDs are all private-label products. |
| `docs_category_weighting.csv` | 8 category rules | Context for how this sample demand may have been generated | Rule table, not an observed warehouse signal. |
| `docs_customer_mix.csv` | 2 customer-mix rules | Context only | Rule table, not an observed outcome. |
| `docs_discount_logic.csv` | 4 channel rules | Optional pricing context | Irrelevant to pick-slot accuracy. |
| `docs_tax_logic.csv` | 3 tax rules | Optional sales-report context | Irrelevant to slotting. |
| `sales_channel_lookup.csv` | 4 channels | Normalise channel labels | No warehouse assignment or pick event mapping. |
| `the_kings_pantry_powerbi_theme.json` | Colour/type theme | Optional UI styling | No model data. |
| `supply_chain_data.csv` | 100 SKU-level rows | Possible supply-chain dashboard exercise | SKU IDs (`SKU0` etc.) do not join the product IDs above; `Location` contains five cities, not bin coordinates. No dated transactions or pick paths. The two lead-time columns differ in 99 rows and need definition. Do not merge with the King's Pantry files. |

All references from sales lines to order and product IDs resolve, and all order customer IDs resolve as strings, but duplicate customer IDs make some joins ambiguous. Product brand IDs and nonblank vendor IDs resolve. Of the 47,156 sales lines, 32,272 refer to products marked stock, active and not discontinued, covering 370 products. This flag filter is only a candidate for demand modelling; it is not evidence that every remaining line was physically picked. For those 370 products over 92 days, 42.89% of product-days have no candidate order line. The file names and fantasy-themed entities suggest an illustrative dataset; provenance and generation method still require confirmation.

## Missing first-party data contract

Obtain from an actual WMS/ERP and a surveyed warehouse, with stable IDs and timestamps:

1. **Facility and routes:** warehouses, zones, racks, bin IDs, usable dimensions, load limits, reach heights, coordinates, depot/packing points, walkable aisle graph, one-way restrictions, closures, travel modes, and route changes.
2. **SKU and inventory:** eaches/cases/pallet dimensions and weights, orientation rules, temperature/hazard/segregation/expiry rules, on-hand and reserved stock by bin, multiple pick faces, replenishment minimums and pick-face capacities.
3. **Workload:** order lines, allocation, cancellations, waves/batches, actual pick quantity, bin scanned, picker/task timestamps, replenishment tasks, shifts, and congestion periods. Preserve the distinction between ordered, allocated, picked, and shipped units.
4. **Current state and outcomes:** timestamped current assignments and move history; measured travel distance or calibrated paths; pick, handling and replenishment time; service, error and labour outcomes. These are needed to calibrate estimates and measure the impact of a pilot.

Every input should have a source, owner, refresh cadence, units, validity window, and data-quality threshold. Reject or quarantine invalid rows; never silently substitute synthetic locations in a production tenant.

## Public datasets for development and external benchmarking

Public data supplements engineering tests; it cannot replace a customer's own warehouse geometry or operational logs.

| Source | Use | Qualification |
| --- | --- | --- |
| [Footwear warehouse order-picking dataset](https://data.mendeley.com/datasets/pf2w725pw3/1) | Real WMS-derived product/location/picking-wave and layout data for route and slotting benchmark work. | Dataset page lists CC BY 4.0; the accompanying paper notes anonymisation/randomisation and a single facility. Check attribution and current terms before product use. |
| [Storage assignment and picker routing instances](https://zenodo.org/records/7866860) | Verify an optimiser against published small instances and detailed results. | Research benchmark instances, not a customer's live operation; review dataset rights. |
| [M5 retail forecasting competition data](https://www.kaggle.com/c/m5-forecasting-accuracy/data) | Benchmark forecasting methods on longer dated retail demand history. | Sales demand only, no warehouse slots/routes. Competition rules govern use; do not redistribute or mix customer records with this benchmark. |

## Engineering sequence

### 1. Define the product boundary and acceptance metrics

Make slotting recommendations advisory, require authorised review, and use explicit objectives: pick travel/time, replenishment, space, move cost, safety and service. Do not use a generic "90–95% accurate" claim. Pre-register separate metrics and thresholds with the first pilot customer:

- Hard-rule violations: **zero**; any violation blocks a proposal.
- Input coverage and freshness: report eligible SKUs, bins, order lines, unknown attributes and last refresh; incomplete required data blocks the affected recommendation.
- Forecast quality: rolling, time-ordered SKU/warehouse and aggregate backtests using WAPE/MAE or a suitable intermittent-demand metric, compared with seasonal-naive and recent-average baselines. State horizon, cohort and uncertainty intervals. A 90–95% forecast-accuracy target can only be interpreted after defining a particular metric and cohort; it cannot be promised for every SKU.
- Operational model quality: compare predicted versus observed route distance, pick time and replenishments on held-out historical tasks; measure calibration error by facility and workload segment. A proposed <=10% prediction error is a pilot target, not a current result.
- Business impact: in a controlled pilot, compare measured cost per pick/order, replenishment workload, errors and service levels against a matched baseline, with uncertainty and rollback rules.

### 2. Build production data ingestion

Create typed versioned schemas and tenant-scoped WMS/ERP adapters. Normalise units and time zones, reconcile identities, deduplicate with an explicit rule, validate foreign keys and state transitions, record source lineage and freshness, and quarantine unresolved rows. De-identify or omit customer personal fields when only demand aggregation is needed.

### 3. Replace the demo cost and assignment model

Replace the aisle/bay arithmetic with shortest paths on a surveyed aisle graph, calibrated to observed travel. Model multi-SKU bins, multiple locations per SKU, inventory quantities, ergonomic and safety restrictions, picking waves, congestion, replenishment and move costs. Use a constrained optimisation method (for example mixed-integer programming or CP-SAT on an appropriately scoped problem), with feasible fallback and documented optimality gap/time limit. Keep hard constraints separate from weighted business objectives, and explain why each recommendation is feasible and beneficial.

### 4. Validate before deployment

Run temporal backtests with no future leakage and a frozen current-layout snapshot. Test independent facilities where possible. Compare predicted versus actual metrics under the existing layout before trusting counterfactual gains. Run shadow mode without moving inventory, then a limited controlled pilot with staff sign-off, measured outcomes and a rollback path. Expand only after the agreed gates pass.

### 5. Operate as an enterprise service

Add tenant isolation, authentication/RBAC, approval and audit trails, versioned datasets/runs/models, asynchronous jobs, idempotent imports/exports, monitoring for stale or drifting data, backup/restore, observability, secure secrets, deployment tests and WMS integration. Keep the customer-visible source and confidence of every number and never label a simulation as measured savings.

## Immediate next action

Obtain one **actual warehouse export** containing bin master/layout, SKU physical attributes, inventory by bin, timestamped pick/replenishment events and the current assignment. Map it to the contract above. The supplied sales files can be used separately to prototype demand ingestion, after resolving the customer duplicate and defining an order-status policy. Until that real export exists, public benchmarks can improve the algorithm but cannot establish customer-specific 90–95% accuracy.
