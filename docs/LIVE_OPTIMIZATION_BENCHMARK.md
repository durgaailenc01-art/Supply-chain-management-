# Live optimization benchmark (2026-11-01)

## Metric

The UI's `optimization_score` is percentage change in modeled pick travel against the uploaded current layout. It is not recommendation accuracy, a forecast accuracy rate, or measured operational savings. No held-out real-world placement outcomes are available for calculating accuracy.

## WH-DEMO-01 replay

Input: the 16 CSV files in the user's `slotwise_warehouse_test_dataset`, snapshot `2026-11-01T00:00:00Z`. This package has a synthetic fixture approval reference and no externally verified certificate.

| Measurement | Result |
| --- | ---: |
| Current weighted round-trip travel | 1,530 m |
| Modeled score | 64.71% |
| Assigned / unassigned SKUs | 2 / 1 |
| Optimistic score ceiling, ignoring SKU compatibility | 75.95% |
| Minimum eligible round trip | 8 m |

The 85% target would require at most 229.5 m of weighted travel. Even placing all 46 picks at the closest eligible bin requires 368 m. Thus 85% is impossible under this layout and workload, even before capacity, bin-sharing and hazard restrictions. The six certificates for 100 slow-SKU packs each exceed bin weight, volume and geometry limits, so those certificates cannot be used as placement evidence.

## Public benchmark candidates

- [Footwear warehouse order-picking dataset](https://data.mendeley.com/datasets/pf2w725pw3/1): WMS-derived orders, waves, product and storage location files plus layout geometry; useful for demand and routing evaluation. The listed files do not establish the physical SKU package measurements, certified capacity, hazard permissions and coherent inventory snapshot required for safe Slotwise placements.
- [Storage assignment and picker-routing benchmark instances and detailed results](https://zenodo.org/records/7866860): useful for comparing algorithmic travel quality against published instances. Its results are for a different problem specification and cannot be treated as Slotwise operational ground truth.
- [Warehouse order-batching benchmark](https://github.com/zalandoresearch/batching-benchmarks): provides generated articles, orders, parameters and warehouse items for routing or batching experiments; not site-certified storage data.

These sources should be evaluated in a separate benchmark adapter. Missing physical measurements or certificates must remain missing; benchmark assumptions cannot be represented as verified warehouse facts. A real accuracy evaluation needs time-stamped WMS snapshots, measured picks and travel, approved package and bin capacities, and a held-out period or controlled site trial.

## Downloaded source inspection and adapter runs

The source archives are retained under `data/benchmarks/footwear` and `data/benchmarks/slaprp`. `backend.benchmark_adapters` maps only published fields into canonical column layouts; unavailable values remain blank. Example partial CSV projections and `missing_fields.json` are under `data/benchmarks/canonical_partial`. These are **not** operationally importable snapshots. `python -m backend.benchmark_public` produces `data/benchmarks/profile.json` from the downloaded source files.

| Source scope | Source records | Adapter runtime | Full Slotwise feasibility | Slotwise assignment coverage | Comparable objective gap | Slotwise travel improvement |
| --- | ---: | ---: | --- | --- | --- | --- |
| Footwear, 100 orders | 689 order lines | 0.02 s | Unknown: missing physical/safety/aisle edges | N/A | N/A | N/A |
| Footwear, 1,000 orders | 4,411 order lines | 0.03 s | Unknown | N/A | N/A | N/A |
| Footwear, all 32,634 orders | 122,370 order lines | 0.68 s | Unknown | N/A | N/A | N/A |
| SLAPRP Guo, 90 instances | 40 SKUs; 50–208 orders each | Mean 0.0019 s, max 0.0256 s parse time | Unknown: no canonical physical/safety snapshot | N/A; published fixed assignments cover 60–80% of SKU IDs | N/A | N/A |
| SLAPRP Silva, 30 small + 30 regular instances | 20–200 SKUs; 1–10 aisles | Included in 150-instance average below | Unknown | N/A | N/A | N/A |

The SLAPRP workbook reports a zero *published solver* gap for these 90 Guo instances. That is not a Slotwise gap. Its published objective combines storage assignment and the benchmark's picker-routing policy. Slotwise currently models per-SKU depot round trips, so comparing the numeric objective values would be invalid. Source-only adapters have no observed picking travel, certified bin capacity, hazard permissions, package measurements, or coherent inventory timestamp. None was manufactured for the benchmark.

The first adapter profile was repeated three times over the same 90 Guo instances and three footwear scopes. Source counts were identical; total parse/profile runtimes were 0.894 s, 0.879 s and 0.859 s. The expanded profile covers 150 instances, including Silva small and regular layouts; its mean instance parse time is 0.0035 s, maximum 0.0377 s. These are adapter timings, not Slotwise optimization runtimes. One selected Silva instance has no matching reference row in the downloaded detailed-results workbook.

## Engine changes and regression

- Picker shortest paths now exclude inactive aisle nodes. The former path calculation could use an inactive node as a shortcut even though validation had ruled it out.
- After greedy placement, initially unassigned SKUs get a second feasibility-checked chance once other moves have freed bins. This does not override capacity or policy checks.
- The full backend suite passes 42 tests; the frontend production build passes. The actual WH-DEMO-01 upload was replayed three times with identical results: 2 assigned, 1 unassigned, 64.71% modeled travel improvement score, 75.95% optimistic ceiling. End-to-end in-process import plus optimization took 0.0646 s, 0.0303 s and 0.0269 s (excluding network/UI overhead). These values are not measured operational accuracy.
