# Warehouse Slotting Optimizer

A reproducible, CSV-backed decision-support demo for placing SKUs in warehouse pick locations. The generator creates master data, orders, baseline assignments, pick events, replenishments, and five labelled exception fixtures. The optimizer applies physical and compatibility rules before comparing simulated travel distance on the **same orders**.

## Quick start

Python 3.11+ and Node 20+ are required. No database or cloud account is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
python -m backend.generate --scale starter
python -m backend.optimize --dataset data/starter
uvicorn backend.app:app --reload
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL. API docs are at `http://127.0.0.1:8000/docs`. The default API reads `data/starter`. Set `SLOTTING_DATA_DIR` to another dataset directory before launching the API. In development, Vite proxies `/api` requests to FastAPI on port 8000; the UI reads all dataset values from those endpoints. For separate deployment origins, set `VITE_API_BASE_URL` in `frontend/.env` and include the frontend origin in `SLOTTING_CORS_ORIGINS` for the API. Example values are in each `.env.example` file.

## Full dataset and reproducibility

```powershell
python -m backend.generate --scale full
python -m backend.optimize --dataset data/full
python -m backend.validate --dataset data/full
python -m backend.reproducibility --scale starter
python -m backend.export --dataset data/starter
python -m backend.demand_forecast --dataset data/starter
```

The full scale generates 20 warehouses, 10,000 locations, 5,000 SKUs and 100,000 order lines. Full generated data is ignored by Git; commit the small starter data and regenerate the full dataset for evaluation. The export command writes JSON and SQLite-compatible `seed.sql`. Each dataset contains a manifest with row counts and SHA-256 checksums. All data is synthetic, generated with seed `20261022`.

## Architecture and method

`backend/generate.py` creates immutable input CSV files and separate labelled exception fixtures. `backend/engine.py` performs candidate filtering, global one-SKU-per-location assignment, pick and replenishment simulation, validation, and metrics. `backend/app.py` serves JSON APIs. React displays the results and can trigger a fresh optimization run. Proposed assignment records, recommendations, and metrics are stored under `data/<scale>/runs/<run_id>/`, with the source manifest checksum and algorithm configuration recorded in each run. The generator never overwrites an existing dataset unless `--force` is given.

Hard constraints: same warehouse, active/unblocked slot, compatible temperature and hazard class, fitting dimensions, enough weight/volume capacity, and one active SKU per location. Oversized SKUs are placed first because they have the fewest options. Remaining eligible SKUs are ranked by observed order-line picks. Eligible locations are ranked by walking distance from the dock, with larger pick faces breaking distance ties. A deterministic greedy matching reserves each selected location globally. The baseline is a valid seeded random placement independent of demand. This is an explainable heuristic, not a claim of global optimality.

Distance is a simulated round-trip aisle walk: `2 * (aisle * 4 + bay * 2)` metres per pick. The same order lines and geometry are used for baseline and optimized results. Picking time assumes 1.2 m/s walk speed plus 15 seconds handling per line. Replenishment count is `ceil(total ordered units / floor(location capacity / SKU unit volume)) - 1`, bounded below by zero. Space utilization uses a stock proxy of up to one pick-face capacity of observed ordered units per SKU; it is not an inventory measurement. All results are estimates, not measured operational savings.

See [data dictionary](docs/data_dictionary.md), [relationships and lifecycle](docs/architecture.md), [business rules](docs/business_rules.md), [validation report](docs/validation_report.md), and [demo guide](docs/demo_guide.md). Exceptions are in `ground_truth.csv` and are excluded from optimizer inputs. The fixture detector reports physical overcapacity, incompatible co-location, duplicate active location, blocked aisle, and labor overload.

## Demo flow

1. Generate starter data and run optimization.
2. Open the dashboard and compare baseline versus proposed simulated distance and time.
3. Inspect a recommendation, its reason and physical constraints.
4. Open exceptions to show detection of all five labelled cases.
5. Run validation and reproducibility commands, then show the manifest.

## API

`GET /api/overview?warehouse_id=WH-001`, `GET /api/warehouses`, `GET /api/recommendations?warehouse_id=WH-001&search=SKU-000007&sort=impact&limit=20&offset=0`, `GET /api/exceptions`, `GET /api/validation`, `GET /api/demand-forecast`, `POST /api/optimize`, `GET /api/export/{table}`. Omitting `warehouse_id` returns the all-warehouse overview. Recommendation sorting supports `impact`, `frequency`, and `sku`. The export endpoint serves CSV for an allowlisted table. The optional demand forecast uses pandas, NumPy, and scikit-learn with a chronological 14-day holdout and reports its error against a rolling-mean baseline. It does not override physical slotting constraints. The API is designed for a local, single-user demo; no authentication or concurrent writes are provided.
