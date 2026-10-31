"""Local demo API. Run with: uvicorn backend.app:app --reload"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .engine import detect_exceptions, metrics as calculate_metrics, optimize_dataset, read_csv, workload
from .validate import validate_dataset

DATASET = Path(os.getenv("SLOTTING_DATA_DIR", "data/starter"))
app = FastAPI(title="Warehouse Slotting Optimizer", version="0.1.0")
cors_origins = [origin.strip() for origin in os.getenv("SLOTTING_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins,
                   allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


def current_run() -> tuple[Path, dict]:
    if not (DATASET / "manifest.json").exists():
        raise HTTPException(503, "Generate the dataset first: python -m backend.generate --scale starter")
    latest = DATASET / "latest_run.json"
    if not latest.exists():
        result = optimize_dataset(DATASET)
        run_id = result["run_id"]
    else:
        run_id = json.loads(latest.read_text(encoding="utf-8"))["run_id"]
    run_dir = DATASET / "runs" / run_id
    return run_dir, json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))


@app.get("/api/overview")
def overview(warehouse_id: str | None = None) -> dict:
    run_dir, run_metrics = current_run()
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    warehouse_rows = read_csv(DATASET / "warehouses.csv")
    warehouse = next((row for row in warehouse_rows if row["warehouse_id"] == warehouse_id), None)
    if warehouse_id and warehouse is None:
        raise HTTPException(404, "Warehouse not found")
    if warehouse_id:
        profiles = [p for p in read_csv(DATASET / "sku_profiles.csv") if p["warehouse_id"] == warehouse_id]
        locations = [l for l in read_csv(DATASET / "locations.csv") if l["warehouse_id"] == warehouse_id]
        sku_ids = {p["sku_id"] for p in profiles}
        lines = [line for line in read_csv(DATASET / "order_lines.csv") if line["sku_id"] in sku_ids]
        baseline = [a for a in read_csv(DATASET / "slot_assignments.csv") if a["sku_id"] in sku_ids]
        proposed = [a for a in read_csv(run_dir / "recommendations.csv") if a["sku_id"] in sku_ids]
        picks, units = workload(lines)
        before = calculate_metrics(baseline, locations, profiles, picks, units)
        after = calculate_metrics(proposed, locations, profiles, picks, units)
        run_metrics = {**run_metrics, "baseline": before, "optimized": after,
                       "distance_reduction_pct": round(100 * (1 - after["picking_distance_m"] / before["picking_distance_m"]), 2) if before["picking_distance_m"] else 0}
    return {"metrics": run_metrics,
            "scope": {"warehouse_id": warehouse_id, "name": warehouse["name"] if warehouse else "All warehouses"},
            "dataset": {"scale": manifest["scale"], "seed": manifest["seed"], "source": manifest["source"],
                        "rows": {k: v["rows"] for k, v in manifest["tables"].items()}}}


@app.get("/api/warehouses")
def warehouses() -> list[dict]:
    return read_csv(DATASET / "warehouses.csv")


@app.get("/api/recommendations")
def recommendations(warehouse_id: str | None = None, search: str = "", sort: str = "impact", limit: int = Query(20, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    run_dir, _ = current_run()
    rows = read_csv(run_dir / "recommendations.csv")
    sku_map = {r["sku_id"]: r for r in read_csv(DATASET / "skus.csv")}
    profile_map = {r["sku_id"]: r for r in read_csv(DATASET / "sku_profiles.csv")}
    sku_wh = {sku_id: sku["warehouse_id"] for sku_id, sku in sku_map.items()}
    if warehouse_id:
        rows = [r for r in rows if sku_wh[r["sku_id"]] == warehouse_id]
    query = search.strip().casefold()
    if query:
        rows = [r for r in rows if query in r["sku_id"].casefold() or query in sku_map[r["sku_id"]]["name"].casefold()]
    if sort not in {"impact", "frequency", "sku"}:
        raise HTTPException(422, "Invalid sort option")
    if sort == "impact":
        rows.sort(key=lambda r: (-(int(r["pick_frequency"]) * (int(r["baseline_distance_m"] or 0) - int(r["optimized_distance_m"] or 0))), r["sku_id"]))
    elif sort == "frequency":
        rows.sort(key=lambda r: (-int(r["pick_frequency"]), r["sku_id"]))
    else:
        rows.sort(key=lambda r: r["sku_id"])
    items = []
    for row in rows[offset:offset + limit]:
        old = int(row["baseline_distance_m"]) if row["baseline_distance_m"] else None
        new = int(row["optimized_distance_m"]) if row["optimized_distance_m"] else None
        items.append({**row, "warehouse_id": sku_wh[row["sku_id"]],
                      "sku_name": sku_map[row["sku_id"]]["name"], "category": sku_map[row["sku_id"]]["category"],
                      "temperature": profile_map[row["sku_id"]]["temperature"],
                      "hazard_class": profile_map[row["sku_id"]]["hazard_class"],
                      "distance_saving_m": old - new if old is not None and new is not None else None,
                      "weighted_distance_saving_m": int(row["pick_frequency"]) * (old - new) if old is not None and new is not None else None,
                      "decision": "unassigned" if not row["location_id"] else "keep" if row["baseline_location_id"] == row["location_id"] else "move"})
    return {"total": len(rows), "limit": limit, "offset": offset, "items": items}


@app.get("/api/exceptions")
def exceptions() -> dict:
    detected = detect_exceptions(read_csv(DATASET / "exception_fixtures.csv"),
                                 read_csv(DATASET / "sku_profiles.csv"), read_csv(DATASET / "locations.csv"))
    truth = {r["case_id"]: r for r in read_csv(DATASET / "ground_truth.csv")}
    return {"items": [{**r, "expected_issue": truth[r["case_id"]]["issue_type"],
                       "expected_decision": truth[r["case_id"]]["expected_decision"],
                       "correct": r["detected_issue"] == truth[r["case_id"]]["issue_type"]} for r in detected]}


@app.get("/api/validation")
def validation() -> dict:
    return validate_dataset(DATASET)


@app.get("/api/demand-forecast")
def demand_forecast(top_n: int = Query(20, ge=1, le=100)) -> dict:
    from .demand_forecast import forecast
    return forecast(DATASET, top_n=top_n)


@app.post("/api/optimize")
def optimize() -> dict:
    if not (DATASET / "manifest.json").exists():
        raise HTTPException(503, "Dataset missing")
    return optimize_dataset(DATASET)


@app.get("/api/export/{table}")
def export(table: str):
    allowed = {"warehouses", "zones", "locations", "skus", "sku_profiles", "orders", "order_lines",
               "slot_assignments", "pick_events", "replenishments", "exception_fixtures", "ground_truth"}
    if table not in allowed:
        raise HTTPException(404, "Unknown table")
    return FileResponse(DATASET / f"{table}.csv", filename=f"{table}.csv", media_type="text/csv")
