"""Deterministic synthetic warehouse dataset generator."""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .engine import assign, checksum, distance, read_csv, unit_capacity, workload, write_csv

SEED = 20261022
SCALES = {"starter": (2, 200, 100, 2000), "full": (20, 10000, 5000, 100000)}
ZONE_TYPES = ("ambient", "bulk", "chilled", "hazmat")
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def generate(scale: str, destination: Path, *, force: bool = False) -> dict:
    if destination.exists() and any(destination.iterdir()) and not force:
        raise FileExistsError(f"{destination} already contains data; pass --force to regenerate")
    destination.mkdir(parents=True, exist_ok=True)
    if force:
        (destination / "latest_run.json").unlink(missing_ok=True)
    rng = random.Random(SEED)
    wh_count, loc_count, sku_count, line_count = SCALES[scale]
    warehouses, zones, locations, skus, profiles = [], [], [], [], []
    for w in range(wh_count):
        wid = f"WH-{w+1:03d}"
        warehouses.append({"warehouse_id": wid, "name": f"Demo warehouse {w+1}", "status": "active", "labor_capacity_per_shift": 80})
        for z, zone_type in enumerate(ZONE_TYPES):
            zid = f"ZON-{wid}-{z+1:02d}"
            zones.append({"zone_id": zid, "warehouse_id": wid, "name": zone_type.title(), "zone_type": zone_type, "status": "active"})
            count = loc_count // wh_count // len(ZONE_TYPES)
            for n in range(count):
                hazard = "flammable" if zone_type == "hazmat" and n % 2 == 0 else "corrosive" if zone_type == "hazmat" else "none"
                temp = "chilled" if zone_type == "chilled" else "ambient"
                length, width, height = (160, 120, 120) if zone_type == "bulk" else (70, 60, 60)
                aisle = n // 10 + 1
                bay = n % 10 + 1
                locations.append({
                    "location_id": f"LOC-{zid}-{n+1:06d}", "zone_id": zid, "warehouse_id": wid,
                    "aisle": aisle, "bay": bay, "length_cm": length, "width_cm": width, "height_cm": height,
                    "capacity_cm3": length * width * height, "max_weight_kg": 500 if zone_type == "bulk" else 150,
                    "temperature": temp, "hazard_class": hazard, "blocked": "false", "status": "active",
                })
        for s in range(sku_count // wh_count):
            global_s = w * (sku_count // wh_count) + s + 1
            sid = f"SKU-{global_s:06d}"
            kind = s % 20
            if kind == 0:
                category, hazard, temp, length = "bulk", "none", "ambient", 110
            elif kind in (1, 2, 3):
                category, hazard, temp, length = "chilled", "none", "chilled", 25
            elif kind == 4:
                category, hazard, temp, length = "hazmat", "flammable", "ambient", 20
            elif kind == 5:
                category, hazard, temp, length = "hazmat", "corrosive", "ambient", 20
            else:
                category, hazard, temp, length = "ambient", "none", "ambient", rng.randint(12, 40)
            width = min(length, rng.randint(12, 40))
            height = min(length, rng.randint(10, 35))
            skus.append({"sku_id": sid, "warehouse_id": wid, "name": f"Synthetic {category} item {global_s}", "category": category, "status": "active"})
            profiles.append({"profile_id": f"PRO-{global_s:06d}", "sku_id": sid, "warehouse_id": wid,
                             "length_cm": length, "width_cm": width, "height_cm": height,
                             "weight_kg": 75 if category == "bulk" else rng.randint(1, 25),
                             "temperature": temp, "hazard_class": hazard, "effective_from": START.isoformat(), "effective_to": ""})

    # Orders and order lines share warehouse IDs, dates, and immutable counters.
    orders, lines = [], []
    sku_by_wh = {wh["warehouse_id"]: [s["sku_id"] for s in skus if s["warehouse_id"] == wh["warehouse_id"]] for wh in warehouses}
    order_count = line_count // 4
    for o in range(order_count):
        oid = f"ORD-2026-{o+1:06d}"
        wid = warehouses[o % wh_count]["warehouse_id"]
        day = o % 90
        ordered_at = (START + timedelta(days=day, hours=o % 24)).isoformat()
        orders.append({"order_id": oid, "warehouse_id": wid, "ordered_at": ordered_at, "status": "fulfilled"})
        choices = sku_by_wh[wid]
        weights = [1 / ((i + 1) ** 0.8) * (2.5 if day >= 60 and i % 10 == 0 else 1) for i in range(len(choices))]
        for j, sid in enumerate(rng.choices(choices, weights=weights, k=4)):
            lines.append({"order_line_id": f"OL-{o+1:06d}-{j+1:02d}", "order_id": oid,
                          "sku_id": sid, "quantity": rng.randint(1, 4), "line_number": j+1})

    picks, units = workload(lines)
    blocked_loc = next(l for l in reversed(locations) if l["zone_id"].endswith("-01"))
    blocked_loc["blocked"] = "true"
    baseline = assign(profiles, locations, picks, baseline=True)
    baseline_rows = []
    for i, row in enumerate(baseline, 1):
        baseline_rows.append({"slot_assignment_id": f"SLOT-2026-{i:06d}", "sku_id": row["sku_id"],
                              "location_id": row["location_id"], "status": "active" if row["location_id"] else "unassigned",
                              "valid_from": START.isoformat(), "valid_to": "", "run_id": "BASELINE-2026"})
    loc_map = {l["location_id"]: l for l in locations}
    assignment_map = {a["sku_id"]: a["location_id"] for a in baseline_rows}
    pick_events = []
    for line in lines:
        locid = assignment_map[line["sku_id"]]
        pick_events.append({"pick_event_id": f"PICK-{line['order_id']}-{int(line['line_number']):03d}",
                            "order_line_id": line["order_line_id"], "location_id": locid,
                            "picked_at": (START + timedelta(days=90)).isoformat(),
                            "distance_m": distance(loc_map[locid]) if locid else 0, "status": "completed" if locid else "exception"})
    replenishments = []
    profile_map = {p["sku_id"]: p for p in profiles}
    for idx, assignment in enumerate(baseline_rows, 1):
        sid, locid = assignment["sku_id"], assignment["location_id"]
        if not locid:
            continue
        capacity = unit_capacity(profile_map[sid], loc_map[locid])
        tasks = max(0, (units[sid] + capacity - 1) // capacity - 1)
        replenishments.append({"replenishment_id": f"REP-2026-{idx:06d}", "sku_id": sid,
                               "location_id": locid, "tasks": tasks, "unit_capacity": capacity,
                               "requested_at": (START + timedelta(days=90)).isoformat(), "status": "simulated"})

    # Fixture cases are separate from clean baseline files. Labels exist only in ground_truth.csv.
    bulk_sku = next(p for p in profiles if p["length_cm"] == 110)
    small_loc = next(l for l in locations if l["warehouse_id"] == bulk_sku["warehouse_id"] and l["zone_id"].endswith("-01"))
    flammable = next(p for p in profiles if p["hazard_class"] == "flammable")
    corrosive = next(p for p in profiles if p["hazard_class"] == "corrosive")
    haz_loc = next(l for l in locations if l["hazard_class"] == "flammable")
    ambient_skus = [p for p in profiles if p["hazard_class"] == "none" and p["temperature"] == "ambient" and p["length_cm"] < 70]
    regular_loc = next(l for l in locations if l["zone_id"].endswith("-01"))
    fixtures = [
        {"case_id": "CASE-001", "sku_id": bulk_sku["sku_id"], "location_id": small_loc["location_id"], "second_sku_id": "", "path_aisle": "", "replenishment_tasks": "", "labor_capacity": ""},
        {"case_id": "CASE-002", "sku_id": flammable["sku_id"], "location_id": haz_loc["location_id"], "second_sku_id": corrosive["sku_id"], "path_aisle": "", "replenishment_tasks": "", "labor_capacity": ""},
        {"case_id": "CASE-003", "sku_id": ambient_skus[0]["sku_id"], "location_id": regular_loc["location_id"], "second_sku_id": ambient_skus[1]["sku_id"], "path_aisle": "", "replenishment_tasks": "", "labor_capacity": ""},
        {"case_id": "CASE-004", "sku_id": ambient_skus[0]["sku_id"], "location_id": blocked_loc["location_id"], "second_sku_id": "", "path_aisle": blocked_loc["aisle"], "replenishment_tasks": "", "labor_capacity": ""},
        {"case_id": "CASE-005", "sku_id": ambient_skus[0]["sku_id"], "location_id": regular_loc["location_id"], "second_sku_id": "", "path_aisle": "", "replenishment_tasks": "12", "labor_capacity": "8"},
    ]
    truth = [
        {"case_id": "CASE-001", "issue_type": "capacity_exceeded", "expected_decision": "reject"},
        {"case_id": "CASE-002", "issue_type": "incompatible_co_location", "expected_decision": "flag"},
        {"case_id": "CASE-003", "issue_type": "duplicate_active_location", "expected_decision": "flag"},
        {"case_id": "CASE-004", "issue_type": "blocked_aisle", "expected_decision": "flag"},
        {"case_id": "CASE-005", "issue_type": "labor_overload", "expected_decision": "flag"},
    ]
    tables = {"warehouses": warehouses, "zones": zones, "locations": locations, "skus": skus,
              "sku_profiles": profiles, "orders": orders, "order_lines": lines,
              "slot_assignments": baseline_rows, "pick_events": pick_events,
              "replenishments": replenishments, "exception_fixtures": fixtures, "ground_truth": truth}
    for name, rows in tables.items():
        write_csv(destination / f"{name}.csv", rows)
    manifest = {"seed": SEED, "scale": scale, "source": "fully synthetic; no external data",
                "tables": {name: {"rows": len(rows), "sha256": checksum(destination / f"{name}.csv")} for name, rows in tables.items()}}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=SCALES, default="starter")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    target = args.output or Path("data") / args.scale
    result = generate(args.scale, target, force=args.force)
    print(json.dumps({"directory": str(target), "rows": {k: v["rows"] for k, v in result["tables"].items()}}, indent=2))


if __name__ == "__main__":
    main()
