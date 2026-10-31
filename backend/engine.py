"""Pure CSV-backed slotting, simulation, and exception rules."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def volume(row: dict) -> float:
    return float(row["length_cm"]) * float(row["width_cm"]) * float(row["height_cm"])


def distance(location: dict) -> int:
    return 2 * (int(location["aisle"]) * 4 + int(location["bay"]) * 2)


def fits(profile: dict, location: dict) -> bool:
    return (
        profile["warehouse_id"] == location["warehouse_id"]
        and location["status"] == "active"
        and location["blocked"] == "false"
        and profile["temperature"] == location["temperature"]
        and profile["hazard_class"] == location["hazard_class"]
        and all(float(profile[f]) <= float(location[f]) for f in ("length_cm", "width_cm", "height_cm"))
        and float(profile["weight_kg"]) <= float(location["max_weight_kg"])
        and volume(profile) <= float(location["capacity_cm3"])
    )


def unit_capacity(profile: dict, location: dict) -> int:
    return max(1, math.floor(float(location["capacity_cm3"]) / volume(profile)))


def assign(profiles: list[dict], locations: list[dict], frequencies: Counter, *, baseline: bool) -> list[dict]:
    by_wh = defaultdict(list)
    for location in locations:
        by_wh[location["warehouse_id"]].append(location)
    reserved: set[str] = set()
    baseline_rng = random.Random(20261022)
    result = []
    # Place dimension-constrained stock first so flexible small SKUs cannot consume its only slots.
    ordering = (lambda p: (-int(float(p["length_cm"]) > 70), p["sku_id"])) if baseline else (lambda p: (-int(float(p["length_cm"]) > 70), -frequencies[p["sku_id"]], p["sku_id"]))
    for profile in sorted(profiles, key=ordering):
        candidates = [loc for loc in by_wh[profile["warehouse_id"]] if loc["location_id"] not in reserved and fits(profile, loc)]
        if not candidates:
            result.append({"sku_id": profile["sku_id"], "location_id": "", "score": "", "reason": "No compatible available slot"})
            continue
        if baseline:
            chosen = baseline_rng.choice(candidates)
        else:
            candidates.sort(key=lambda loc: (distance(loc), -unit_capacity(profile, loc), loc["location_id"]))
            chosen = candidates[0]
        reserved.add(chosen["location_id"])
        result.append({
            "sku_id": profile["sku_id"], "location_id": chosen["location_id"],
            "score": round(1000 / (1 + distance(chosen)) + min(unit_capacity(profile, chosen), 100) / 10000, 4),
            "reason": f"{frequencies[profile['sku_id']]} picks; {distance(chosen)} m route; capacity {unit_capacity(profile, chosen)} units; compatible {profile['temperature']}/{profile['hazard_class']}",
        })
    return result


def workload(order_lines: list[dict]) -> tuple[Counter, Counter]:
    picks: Counter = Counter()
    units: Counter = Counter()
    for line in order_lines:
        picks[line["sku_id"]] += 1
        units[line["sku_id"]] += int(line["quantity"])
    return picks, units


def metrics(assignments: list[dict], locations: list[dict], profiles: list[dict], picks: Counter, units: Counter) -> dict:
    loc_map = {x["location_id"]: x for x in locations}
    profile_map = {x["sku_id"]: x for x in profiles}
    total_distance = 0
    replenishments = 0
    assigned = 0
    used_volume = 0.0
    available_volume = 0.0
    for loc in locations:
        if loc["status"] == "active" and loc["blocked"] == "false":
            available_volume += float(loc["capacity_cm3"])
    for item in assignments:
        if not item["location_id"]:
            continue
        assigned += 1
        loc = loc_map[item["location_id"]]
        profile = profile_map[item["sku_id"]]
        total_distance += picks[item["sku_id"]] * distance(loc)
        # Treat up to one pick-face capacity of observed ordered units as a stock proxy.
        used_volume += volume(profile) * min(units[item["sku_id"]], unit_capacity(profile, loc))
        replenishments += max(0, math.ceil(units[item["sku_id"]] / unit_capacity(profile, loc)) - 1)
    pick_count = sum(picks.values())
    seconds = total_distance / 1.2 + pick_count * 15
    return {
        "assigned_skus": assigned, "unassigned_skus": len(assignments) - assigned,
        "pick_lines": pick_count, "picking_distance_m": total_distance,
        "picking_time_hours": round(seconds / 3600, 2),
        "throughput_lines_per_hour": round(pick_count / (seconds / 3600), 2) if seconds else 0,
        "replenishment_tasks": replenishments,
        "space_utilization_pct": round(100 * used_volume / available_volume, 2) if available_volume else 0,
    }


def detect_exceptions(fixtures: list[dict], profiles: list[dict], locations: list[dict]) -> list[dict]:
    profile_map = {p["sku_id"]: p for p in profiles}
    loc_map = {l["location_id"]: l for l in locations}
    results = []
    for case in fixtures:
        sku = profile_map[case["sku_id"]]
        loc = loc_map[case["location_id"]]
        if case["second_sku_id"]:
            second = profile_map[case["second_sku_id"]]
            issue = "incompatible_co_location" if sku["hazard_class"] != second["hazard_class"] else "duplicate_active_location"
        elif case["path_aisle"]:
            issue = "blocked_aisle" if loc["blocked"] == "true" and int(case["path_aisle"]) == int(loc["aisle"]) else "none"
        elif case["replenishment_tasks"]:
            issue = "labor_overload" if int(case["replenishment_tasks"]) > int(case["labor_capacity"]) else "none"
        else:
            issue = "capacity_exceeded" if not fits(sku, loc) else "none"
        results.append({"case_id": case["case_id"], "detected_issue": issue})
    return results


def optimize_dataset(dataset: Path, *, run_id: str | None = None) -> dict:
    profiles = read_csv(dataset / "sku_profiles.csv")
    locations = read_csv(dataset / "locations.csv")
    lines = read_csv(dataset / "order_lines.csv")
    baseline = read_csv(dataset / "slot_assignments.csv")
    picks, units = workload(lines)
    proposed = assign(profiles, locations, picks, baseline=False)
    baseline_metrics = metrics(baseline, locations, profiles, picks, units)
    optimized_metrics = metrics(proposed, locations, profiles, picks, units)
    baseline_map = {x["sku_id"]: x["location_id"] for x in baseline}
    loc_map = {x["location_id"]: x for x in locations}
    recommendations = []
    for row in proposed:
        old = baseline_map.get(row["sku_id"], "")
        new = row["location_id"]
        recommendations.append({
            **row, "baseline_location_id": old,
            "baseline_distance_m": distance(loc_map[old]) if old else "",
            "optimized_distance_m": distance(loc_map[new]) if new else "",
            "pick_frequency": picks[row["sku_id"]],
        })
    run_id = run_id or datetime.now(timezone.utc).strftime("RUN-%Y%m%dT%H%M%S%fZ")
    run_dir = dataset / "runs" / run_id
    if run_dir.exists():
        raise FileExistsError(f"Run already exists: {run_id}")
    run_dir.mkdir(parents=True)
    write_csv(run_dir / "recommendations.csv", recommendations)
    proposed_assignments = [{
        "slot_assignment_id": f"SLOT-2026-{run_id.removeprefix('RUN-').replace('T', '').replace('Z', '')}-{i:06d}",
        "sku_id": row["sku_id"], "location_id": row["location_id"],
        "status": "proposed" if row["location_id"] else "unassigned",
        "valid_from": datetime.now(timezone.utc).isoformat(), "valid_to": "", "run_id": run_id,
    } for i, row in enumerate(proposed, 1)]
    write_csv(run_dir / "slot_assignments.csv", proposed_assignments)
    output = {
        "run_id": run_id, "dataset": dataset.name, "algorithm": "velocity_greedy_v1",
        "dataset_manifest_sha256": checksum(dataset / "manifest.json"),
        "configuration": {"walk_speed_m_per_s": 1.2, "handling_seconds_per_line": 15,
                          "slot_rule": "nearest eligible; capacity tie break", "replenishment_formula": "ceil(units/unit_capacity)-1"},
        "baseline": baseline_metrics, "optimized": optimized_metrics,
        "distance_reduction_pct": round(100 * (1 - optimized_metrics["picking_distance_m"] / baseline_metrics["picking_distance_m"]), 2) if baseline_metrics["picking_distance_m"] else 0,
    }
    (run_dir / "metrics.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    pointer_tmp = dataset / "latest_run.json.tmp"
    pointer_tmp.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    os.replace(pointer_tmp, dataset / "latest_run.json")
    return output
