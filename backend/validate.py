"""Integrity and arithmetic checks for a generated dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import checksum, detect_exceptions, distance, fits, read_csv, unit_capacity, workload

KEYS = {"warehouses": "warehouse_id", "zones": "zone_id", "locations": "location_id",
        "skus": "sku_id", "sku_profiles": "profile_id", "orders": "order_id",
        "order_lines": "order_line_id", "slot_assignments": "slot_assignment_id",
        "pick_events": "pick_event_id", "replenishments": "replenishment_id",
        "exception_fixtures": "case_id", "ground_truth": "case_id"}


def validate_dataset(dataset: Path) -> dict:
    errors = []
    tables = {name: read_csv(dataset / f"{name}.csv") for name in KEYS}
    ids = {}
    for name, key in KEYS.items():
        values = [r[key] for r in tables[name]]
        ids[name] = set(values)
        if len(values) != len(ids[name]) or "" in ids[name]:
            errors.append(f"{name}: duplicate or empty primary key")
    wh = {x["warehouse_id"]: x for x in tables["warehouses"]}
    zones = {x["zone_id"]: x for x in tables["zones"]}
    loc = {x["location_id"]: x for x in tables["locations"]}
    sku = {x["sku_id"]: x for x in tables["skus"]}
    profiles = {x["sku_id"]: x for x in tables["sku_profiles"]}
    orders = {x["order_id"]: x for x in tables["orders"]}
    lines = {x["order_line_id"]: x for x in tables["order_lines"]}
    for z in tables["zones"]:
        if z["warehouse_id"] not in wh or z["zone_type"] not in {"ambient", "bulk", "chilled", "hazmat"} or z["status"] not in {"active", "inactive"}:
            errors.append(f"{z['zone_id']}: invalid warehouse/type/status")
    for l in tables["locations"]:
        if l["zone_id"] not in zones or l["warehouse_id"] != zones.get(l["zone_id"], {}).get("warehouse_id"):
            errors.append(f"{l['location_id']}: invalid zone/warehouse join")
        if l["status"] not in {"active", "inactive"} or l["blocked"] not in {"true", "false"}:
            errors.append(f"{l['location_id']}: invalid enum")
        if int(l["capacity_cm3"]) != int(l["length_cm"]) * int(l["width_cm"]) * int(l["height_cm"]):
            errors.append(f"{l['location_id']}: capacity arithmetic mismatch")
        if l["temperature"] not in {"ambient", "chilled"} or l["hazard_class"] not in {"none", "flammable", "corrosive"}:
            errors.append(f"{l['location_id']}: invalid compatibility enum")
    for s in tables["skus"]:
        if s["warehouse_id"] not in wh or s["sku_id"] not in profiles or s["status"] not in {"active", "inactive"}:
            errors.append(f"{s['sku_id']}: invalid warehouse/profile/status")
    if len(profiles) != len(sku):
        errors.append("SKU to profile is not one-to-one")
    for p in tables["sku_profiles"]:
        if p["sku_id"] not in sku or p["warehouse_id"] != sku.get(p["sku_id"], {}).get("warehouse_id"):
            errors.append(f"{p['profile_id']}: invalid SKU/warehouse join")
        if p["effective_to"] and p["effective_to"] < p["effective_from"]:
            errors.append(f"{p['profile_id']}: date order")
        if p["temperature"] not in {"ambient", "chilled"} or p["hazard_class"] not in {"none", "flammable", "corrosive"}:
            errors.append(f"{p['profile_id']}: invalid compatibility enum")
    for o in tables["orders"]:
        if o["warehouse_id"] not in wh or o["status"] not in {"open", "fulfilled", "cancelled"}:
            errors.append(f"{o['order_id']}: invalid warehouse/status")
    order_line_pairs = set()
    for line in tables["order_lines"]:
        order = orders.get(line["order_id"], {})
        product = sku.get(line["sku_id"], {})
        if not order or not product or order.get("warehouse_id") != product.get("warehouse_id"):
            errors.append(f"{line['order_line_id']}: invalid order/SKU join")
        if int(line["quantity"]) <= 0:
            errors.append(f"{line['order_line_id']}: invalid quantity")
        pair = (line["order_id"], line["line_number"])
        if pair in order_line_pairs:
            errors.append(f"{line['order_line_id']}: duplicate line number within order")
        order_line_pairs.add(pair)
    active = [a for a in tables["slot_assignments"] if a["status"] == "active"]
    if len({a["location_id"] for a in active}) != len(active) or len({a["sku_id"] for a in active}) != len(active):
        errors.append("duplicate active assignment")
    if {a["sku_id"] for a in tables["slot_assignments"]} != ids["skus"]:
        errors.append("missing or unknown SKU assignment")
    active_location_by_sku = {a["sku_id"]: a["location_id"] for a in active}
    for a in active:
        if a["sku_id"] not in profiles or a["location_id"] not in loc or not fits(profiles[a["sku_id"]], loc[a["location_id"]]):
            errors.append(f"{a['slot_assignment_id']}: invalid physical assignment")
        if a["valid_to"] and a["valid_to"] < a["valid_from"]:
            errors.append(f"{a['slot_assignment_id']}: date order")
    seen_picked_lines = set()
    for event in tables["pick_events"]:
        line = lines.get(event["order_line_id"], {})
        location = loc.get(event["location_id"], {})
        if event["order_line_id"] in seen_picked_lines:
            errors.append(f"{event['pick_event_id']}: duplicate pick for order line")
        seen_picked_lines.add(event["order_line_id"])
        if not line or event["status"] not in {"completed", "exception"} or (event["status"] == "completed" and (not location or int(event["distance_m"]) != distance(location) or active_location_by_sku.get(line["sku_id"]) != event["location_id"])):
            errors.append(f"{event['pick_event_id']}: invalid pick event")
        if line and line["order_id"] in orders and event["picked_at"] < orders[line["order_id"]]["ordered_at"]:
            errors.append(f"{event['pick_event_id']}: pick before order")
    if seen_picked_lines != ids["order_lines"]:
        errors.append("order line/pick event coverage mismatch")
    picks, units = workload(tables["order_lines"])
    latest_order_at = max((o["ordered_at"] for o in tables["orders"]), default="")
    for rep in tables["replenishments"]:
        product = profiles.get(rep["sku_id"], {})
        location = loc.get(rep["location_id"], {})
        if not product or not location:
            errors.append(f"{rep['replenishment_id']}: invalid foreign key")
            continue
        cap = unit_capacity(product, location)
        expected = max(0, (units[rep["sku_id"]] + cap - 1) // cap - 1)
        if int(rep["unit_capacity"]) != cap or int(rep["tasks"]) != expected:
            errors.append(f"{rep['replenishment_id']}: arithmetic mismatch")
        if rep["status"] != "simulated" or rep["requested_at"] < latest_order_at:
            errors.append(f"{rep['replenishment_id']}: lifecycle/date mismatch")
    truth = {r["case_id"]: r["issue_type"] for r in tables["ground_truth"]}
    detected = detect_exceptions(tables["exception_fixtures"], tables["sku_profiles"], tables["locations"])
    for result in detected:
        if result["detected_issue"] != truth.get(result["case_id"]):
            errors.append(f"{result['case_id']}: detector/ground truth mismatch")
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["tables"].items():
        if len(tables[name]) != expected["rows"] or checksum(dataset / f"{name}.csv") != expected["sha256"]:
            errors.append(f"{name}: manifest mismatch")
    return {"valid": not errors, "errors": errors, "row_counts": {k: len(v) for k, v in tables.items()},
            "exception_cases_detected": len(detected), "exception_cases_correct": sum(x["detected_issue"] == truth.get(x["case_id"]) for x in detected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/starter"))
    args = parser.parse_args()
    result = validate_dataset(args.dataset)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)
