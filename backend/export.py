"""Export the CSV snapshot as JSON and an SQLite-compatible SQL seed."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .engine import read_csv
from .validate import KEYS

FOREIGN_KEYS = {
    "zones": {"warehouse_id": "warehouses(warehouse_id)"},
    "locations": {"zone_id": "zones(zone_id)", "warehouse_id": "warehouses(warehouse_id)"},
    "skus": {"warehouse_id": "warehouses(warehouse_id)"},
    "sku_profiles": {"sku_id": "skus(sku_id)", "warehouse_id": "warehouses(warehouse_id)"},
    "orders": {"warehouse_id": "warehouses(warehouse_id)"},
    "order_lines": {"order_id": "orders(order_id)", "sku_id": "skus(sku_id)"},
    "slot_assignments": {"sku_id": "skus(sku_id)", "location_id": "locations(location_id)"},
    "pick_events": {"order_line_id": "order_lines(order_line_id)", "location_id": "locations(location_id)"},
    "replenishments": {"sku_id": "skus(sku_id)", "location_id": "locations(location_id)"},
    "exception_fixtures": {"sku_id": "skus(sku_id)", "location_id": "locations(location_id)", "second_sku_id": "skus(sku_id)"},
    "ground_truth": {"case_id": "exception_fixtures(case_id)"},
}
INTEGERS = {"labor_capacity_per_shift", "aisle", "bay", "length_cm", "width_cm", "height_cm",
            "capacity_cm3", "max_weight_kg", "weight_kg", "quantity", "line_number", "distance_m",
            "tasks", "unit_capacity", "path_aisle", "replenishment_tasks", "labor_capacity"}


def export(dataset: Path) -> dict:
    out = dataset / "exports"
    out.mkdir(exist_ok=True)
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    payload = {}
    for name, primary_key in KEYS.items():
        rows = read_csv(dataset / f"{name}.csv")
        payload[name] = rows
        fields = list(rows[0])
        columns = []
        for field in fields:
            kind = "INTEGER" if field in INTEGERS else "TEXT"
            clause = f'"{field}" {kind}'
            if field == primary_key:
                clause += " PRIMARY KEY"
            if field in FOREIGN_KEYS.get(name, {}):
                clause += f" REFERENCES {FOREIGN_KEYS[name][field]}"
            columns.append(clause)
        connection.execute(f'CREATE TABLE "{name}" ({", ".join(columns)})')
        placeholders = ",".join("?" for _ in fields)
        connection.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})',
                               [tuple(None if r[f] == "" else r[f] for f in fields) for r in rows])
    (out / "dataset.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    (out / "seed.sql").write_text("\n".join(connection.iterdump()) + "\n", encoding="utf-8")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    return {"json": str(out / "dataset.json"), "sql": str(out / "seed.sql"),
            "foreign_key_errors": len(foreign_key_errors)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/starter"))
    args = parser.parse_args()
    print(json.dumps(export(args.dataset), indent=2))
