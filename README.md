# Slotwise — warehouse slotting decision support

Slotwise recommends SKU pick-bin placements from demand, package measurements, bin capacity, compatibility rules, and an aisle graph. It does **not** move physical stock. The **Upload & optimize** workflow analyzes canonical warehouse snapshots. The **legacy demo** generates and evaluates a separate synthetic assessment dataset.

## Fresh clone: install and run

Requires Python 3.11/3.12, Node.js 20+, and npm. No database, cloud account, or API key is needed. Run these PowerShell commands from the repository root:

```powershell
git clone https://github.com/yervavivekanandakumar-cyber/Supply-chain-management-.git
cd Supply-chain-management-
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Use `py -3.11` if Python 3.12 is unavailable. If activation is blocked, invoke `.\.venv\Scripts\python.exe` directly. Start the backend in terminal 1:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in terminal 2:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173/**. API documentation: **http://127.0.0.1:8000/docs**. Keep both terminals running. The backend terminal prints validation, candidate rejections, assignments, and metrics.

## Test the upload workflow

1. Extract [`sample_data/slotwise_20_warehouses.zip`](sample_data/slotwise_20_warehouses.zip), or use the already extracted [`sample_data/slotwise_20_warehouses/`](sample_data/slotwise_20_warehouses/) folder. In **Upload & optimize**, select **all 25 CSV files plus `manifest.json`**. ZIP upload itself is unsupported.
2. The manifest fills the warehouse and snapshot fields. Select `WH-01` through `WH-20`. The snapshot is `2026-10-31T00:00:00Z`, displayed as **31 October 2026, 05:30:00 India time**. Click **Process dataset**. A large import can take 30 seconds or more.
3. Review **Place in**, moves, keeps, unassigned SKUs, validation findings, and modeled travel. Open a row for its reason. Download the placements CSV, stock movements CSV, or Excel workbook. They are proposals for review, not automatic WMS commands.

The revised synthetic `WH-01` sample yielded **83 moves and 167 keeps** in our engine test. This checks the workflow, **not recommendation accuracy**. See [`sample_data/README.md`](sample_data/README.md) for dataset provenance and regeneration.

### Evaluate post-placement observations

The results page accepts an outcome CSV containing `sku_id,actual_location_id,observed_at`; `measured_round_trip_m` is optional. `observed_at` must include a timezone and be after the snapshot. The app reports coverage, placement agreement, move agreement, and route mean absolute error for measured routes. **Agreement means an operator used the proposed bin; it does not prove optimality.** Without independently measured outcomes, recommendation accuracy is **not measured**. [`sample_data/outcome_example.csv`](sample_data/outcome_example.csv) shows the format only and is not real-world evidence.

## Legacy assessment dataset and tests

The committed `data/starter/` works immediately. The legacy generator targets **20 warehouses, 10,000 locations, 5,000 SKUs, and 100,000 order lines** at full scale using seed `20261022`:

```powershell
python -m backend.generate --scale full
python -m backend.optimize --dataset data/full
python -m backend.validate --dataset data/full
python -m backend.reproducibility --scale starter
python -m unittest discover -s backend/tests -q
cd frontend
npm ci
npm run build
```

`data/full/` is Git-ignored and must be generated locally. Labelled `ground_truth.csv` is separate from optimizer inputs. The legacy demo uses a deterministic greedy assignment and simplified simulated aisle distance. Its CSVs are **not complete measured WMS snapshots** for the live uploader.

## Accepted input and limits

The live uploader accepts canonical CSV packages, canonical CSV tables without a manifest, and supported XLSX/XLSM tables. It maps a few documented header aliases. It **cannot safely process arbitrary CSV formats**: warehouse identity, measured bins, package dimensions and weights, inventory, assignments, policies, and aisle access must join correctly. Missing physical or safety values must be supplied by the warehouse, not invented. See the [data contract](docs/WAREHOUSE_DATA_CONTRACT.md), [importer guide](docs/CANONICAL_IMPORTER.md), and [submission readiness assessment](docs/SUBMISSION_READINESS.md).

The live engine uses a constrained heuristic on larger uploads and checked branch-and-bound for at most 8 movable SKUs and 20 candidate bins. Picking travel is a modeled shortest-path depot-to-bin round trip, not measured batch travel. Replenishment cycles are capacity estimates. The optional demand forecast is separate from live slotting. Synthetic site certificates are fictional and only suitable for testing.

This local prototype has no authentication, database, durable analysis storage, or multi-worker coordination. Download results before restarting the backend. Do not use synthetic recommendations for physical stock moves.
