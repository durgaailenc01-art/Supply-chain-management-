"""Upload-driven slotting analysis. The synthetic demo engine remains separate.

This is a deterministic, capacity-aware heuristic. It proposes moves; it never
updates WMS stock or claims observed savings.
"""
from __future__ import annotations

import heapq
import logging
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from .canonical_import import ImportResult, _capacity_excess, _fits, _profile_for

LOG = logging.getLogger("slotwise.live")


def _paths(tables: dict, warehouse: str, departure: str) -> dict[str, float]:
    graph = defaultdict(list)
    reverse = defaultdict(list)
    active_nodes = {node["node_id"] for node in tables["aisle_nodes"]
                    if node["warehouse_id"] == warehouse and node["status"] == "active"}
    if departure not in active_nodes:
        return {}
    for edge in tables["aisle_edges"]:
        if edge["warehouse_id"] != warehouse or edge["status"] != "active" or edge["travel_mode"] not in {"picker", "both"}:
            continue
        a, b, length = edge["from_node_id"], edge["to_node_id"], float(edge["length_m"])
        if a not in active_nodes or b not in active_nodes:
            continue
        graph[a].append((b, length))
        reverse[b].append((a, length))
        if edge["bidirectional"]:
            graph[b].append((a, length))
            reverse[a].append((b, length))
    def shortest(edges):
        distances = {departure: 0.0}
        queue = [(0.0, departure)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance > distances[node]:
                continue
            for next_node, length in edges[node]:
                proposed = distance + length
                if proposed < distances.get(next_node, float("inf")):
                    distances[next_node] = proposed
                    heapq.heappush(queue, (proposed, next_node))
        return distances
    outbound, inbound = shortest(graph), shortest(reverse)
    result = {}
    for access in tables["location_access"]:
        if access["warehouse_id"] != warehouse or access["status"] != "active" or access["travel_mode"] not in {"picker", "both"}:
            continue
        if access["node_id"] in active_nodes and access["node_id"] in outbound and access["node_id"] in inbound:
            round_trip = outbound[access["node_id"]] + inbound[access["node_id"]] + 2 * float(access["walk_offset_m"])
            loc = access["location_id"]
            result[loc] = min(result.get(loc, float("inf")), round_trip)
    return result


def _active(rows: list[dict], snapshot: datetime) -> list[dict]:
    return [r for r in rows if r.get("effective_from", r.get("certified_at", snapshot)) <= snapshot and
            (r.get("effective_to", r.get("valid_until")) is None or snapshot < r.get("effective_to", r.get("valid_until")))]


def optimize_canonical(imported: ImportResult, warehouse: str, warehouse_name: str) -> dict:
    if not imported.valid:
        raise ValueError("The canonical package has validation errors; optimization was not started.")
    if imported.manifest.get("import_kind") != "full_snapshot":
        raise ValueError("A demand-only package has no verified physical bins or inventory.")
    t, snapshot = imported.tables, imported.manifest["snapshot_at"]
    warehouses = {w["warehouse_id"]: w for w in t["warehouses"]}
    if warehouse not in warehouses:
        raise ValueError(f"Warehouse {warehouse!r} does not appear in the package.")
    departure = warehouses[warehouse]["default_pick_node_id"]
    if not departure:
        raise ValueError("Selected warehouse has no default pick departure node.")
    distances = _paths(t, warehouse, departure)
    locations = {r["location_id"]: r for r in t["locations"] if r["warehouse_id"] == warehouse}
    skus = {r["sku_id"]: r for r in t["skus"]}
    packages = {r["packaging_profile_id"]: r for r in t["sku_packaging_profiles"]}
    stock = [r for r in t["inventory_by_location"] if r["warehouse_id"] == warehouse]
    assignment_rows = [r for r in t["slot_assignments"] if r["warehouse_id"] == warehouse and r["status"] == "active" and r["role"] in {"pick", "both"}]
    pick_faces = defaultdict(set)
    for row in assignment_rows:
        pick_faces[row["sku_id"]].add(row["location_id"])
    orders = {o["order_id"]: o for o in t["orders"] if o["warehouse_id"] == warehouse}
    picks, units = Counter(), Counter()
    for line in t["order_lines"]:
        if line["order_id"] in orders and (line["status"] == "fulfilled" or line["status"] == "partially_fulfilled" and line["fulfilled_qty"] is not None and line["fulfilled_qty"] > 0):
            picks[line["sku_id"]] += 1
            units[line["sku_id"]] += float(line["fulfilled_qty"] if line["fulfilled_qty"] is not None else line["ordered_qty"])
    event_picks = Counter()
    event_units = Counter()
    for event in t["pick_events"]:
        if event["warehouse_id"] == warehouse and event["status"] == "completed":
            event_picks[event["sku_id"]] += 1
            event_units[event["sku_id"]] += float(event["picked_qty"])
    demand_source = "fulfilled order lines (pick proxy)" if picks else "completed pick events"
    if not picks:
        picks = event_picks
        units = event_units
    history_from, history_to = imported.manifest.get("history_from"), imported.manifest.get("history_to")
    days = max((history_to - history_from).total_seconds() / 86400, 1) if history_from and history_to else None

    # Available pick stock is movable. Reserve, quarantine and held stock stays in place.
    movable = defaultdict(list)
    current = {}
    for row in stock:
        sku, loc = row["sku_id"], row["location_id"]
        if row["stock_status"] == "available" and loc in pick_faces[sku] and row["reserved_qty"] == 0 and row["hold_qty"] == 0:
            movable[sku].append(row)
            if sku not in current or distances.get(loc, float("inf")) < distances.get(current[sku], float("inf")):
                current[sku] = loc
    for sku, faces in pick_faces.items():
        if sku not in current and faces:
            current[sku] = min(faces, key=lambda loc: (distances.get(loc, float("inf")), loc))

    candidate_ids = set(imported.report["warehouses"][warehouse]["candidate_locations"]) & set(distances)
    blocked_locations = imported.report["warehouses"][warehouse]["blocked_locations"]
    incidents_by_location = defaultdict(list)
    for incident in imported.report["incidents"]:
        if incident["warehouse_id"] == warehouse:
            incidents_by_location[incident["location_id"]].append(incident["detail"])
    for location_id in sorted(locations):
        reasons = incidents_by_location[location_id] or list(blocked_locations.get(location_id, []))
        if location_id not in distances:
            reasons.append("no complete picker round trip from default depot")
        LOG.info("Candidate bin %s | route_m=%s | %s", location_id, distances.get(location_id),
                 "eligible" if location_id in candidate_ids else "rejected: " + ", ".join(sorted(set(reasons))))
    caps = {(r["location_id"], r["packaging_profile_id"]): r["max_handling_unit_count"] for r in t["location_pack_capacities"]
            if r["warehouse_id"] == warehouse and r["certified_at"] <= snapshot and (r["valid_until"] is None or snapshot < r["valid_until"])
            and not _capacity_excess(packages[r["packaging_profile_id"]], locations[r["location_id"]], r["max_handling_unit_count"])}
    plans = defaultdict(list)
    plan_items = defaultdict(dict)
    for row in t["shared_bin_plan_items"]:
        plan_items[row["plan_id"]][row["packaging_profile_id"]] = row["max_handling_unit_count"]
    for plan in t["shared_bin_plans"]:
        if plan["warehouse_id"] == warehouse and plan["certified_at"] <= snapshot and (plan["valid_until"] is None or snapshot < plan["valid_until"]):
            plans[plan["location_id"]].append(plan_items[plan["plan_id"]])
    co = {(min(r["group_a"], r["group_b"]), max(r["group_a"], r["group_b"])): r["allowed"] for r in _active([r for r in t["co_storage_rules"] if r["warehouse_id"] == warehouse], snapshot)}
    hazards = {(min(r["hazard_group_a"], r["hazard_group_b"]), max(r["hazard_group_a"], r["hazard_group_b"])): r for r in _active([r for r in t["hazard_segregation_rules"] if r["warehouse_id"] == warehouse], snapshot)}
    active_profiles = {sku_id: _profile_for(t, sku_id, warehouse, snapshot) for sku_id in {r["sku_id"] for r in stock}}
    hazard_groups = {profile["hazard_group"] for profile in active_profiles.values() if profile}
    unrestricted_hazards = bool(hazard_groups) and all(
        (rule := hazards.get(tuple(sorted((first, second))))) is not None
        and rule["allow_same_location"] and not rule["prohibit_same_zone"]
        and rule["minimum_clearance_m"] == 0 and not rule["barrier_required"]
        for first in hazard_groups for second in hazard_groups
    )
    permissions = {(r["location_id"], r["hazard_group"]) for r in t["location_hazard_permissions"] if r["warehouse_id"] == warehouse}
    separation = {(min(r["location_a_id"], r["location_b_id"]), max(r["location_a_id"], r["location_b_id"])): r for r in t["hazard_separation_evidence"] if r["warehouse_id"] == warehouse}
    occupied = defaultdict(list)
    for row in stock:
        occupied[row["location_id"]].append(row)
    violations = Counter()

    def remove_pick_stock(sku: str) -> None:
        for row in movable[sku]:
            occupied[row["location_id"]].remove(row)

    def restore_pick_stock(sku: str) -> None:
        for row in movable[sku]:
            occupied[row["location_id"]].append(row)

    def feasible(sku: str, destination: str) -> tuple[bool, list[str], float | None]:
        location = locations[destination]
        profile = active_profiles.get(sku)
        reasons = []
        if profile is None:
            return False, ["missing SKU storage profile"], None
        proposed = occupied[destination] + movable[sku]
        distinct = {row["sku_id"] for row in proposed}
        if len(distinct) > location["max_distinct_skus"]:
            reasons.append("distinct SKU limit")
        if not (profile["storage_temp_min_c"] <= location["operating_temp_min_c"] <= location["operating_temp_max_c"] <= profile["storage_temp_max_c"]):
            reasons.append("temperature interval")
        if profile["temperature_class"] == "controlled" and location["temperature_class"] != "controlled":
            reasons.append("controlled temperature class")
        if (destination, profile["hazard_group"]) not in permissions:
            reasons.append("hazard permission")
        for other in distinct - {sku}:
            other_profile = active_profiles.get(other)
            if not other_profile:
                reasons.append("other SKU profile missing")
                continue
            pair = tuple(sorted((profile["co_storage_group"], other_profile["co_storage_group"])))
            if not co.get(pair, False):
                reasons.append("co-storage policy")
            hazard_pair = tuple(sorted((profile["hazard_group"], other_profile["hazard_group"])))
            rule = hazards.get(hazard_pair)
            if not rule or not rule["allow_same_location"]:
                reasons.append("same-bin hazard policy")
        lots = defaultdict(set)
        counts = Counter()
        weight = volume = Decimal(0)
        for row in proposed:
            package = packages[row["packaging_profile_id"]]
            if row["lot_id"]:
                lots[row["sku_id"]].add(row["lot_id"])
            counts[package["packaging_profile_id"]] += row["handling_unit_count"]
            weight += package["gross_weight_kg"] * row["handling_unit_count"]
            volume += package["outer_length_cm"] * package["outer_width_cm"] * package["outer_height_cm"] * row["handling_unit_count"]
            if not _fits(package, location):
                reasons.append("package orientation/size")
        if weight > location["max_total_weight_kg"]:
            reasons.append("weight capacity")
        if volume > location["usable_volume_cm3"]:
            reasons.append("volume capacity")
        if lots and (location["max_lots_per_sku"] is None or location["allow_mixed_lots"] is None):
            reasons.append("unknown lot-mixing policy")
        elif any(len(values) > location["max_lots_per_sku"] or not location["allow_mixed_lots"] and len(values) > 1 for values in lots.values()):
            reasons.append("lot mixing")
        if len(counts) == 1:
            pid, count = next(iter(counts.items()))
            plan_cap = max((plan.get(pid, -1) for plan in plans[destination]), default=-1)
            capacity = max(caps.get((destination, pid), -1), plan_cap)
            if count > capacity:
                reasons.append("certified package count")
            face_capacity = capacity if capacity > 0 else None
            face_units = float(face_capacity * packages[pid]["base_units_per_pack"]) if face_capacity else None
        elif len(counts) > 1:
            matching = [plan for plan in plans[destination] if all(count <= plan.get(pid, -1) for pid, count in counts.items())]
            if not matching:
                reasons.append("joint packing plan")
            sku_package_ids = {row["packaging_profile_id"] for row in movable[sku]}
            face_units = max((float(sum(plan.get(pid, 0) * packages[pid]["base_units_per_pack"] for pid in sku_package_ids)) for plan in matching), default=None)
        else:
            reasons.append("no physical stock")
            face_units = None
        # Apply pairwise physical separation against stock that remains or has already been placed.
        for other_location, other_rows in (() if unrestricted_hazards else occupied.items()):
            if other_location == destination or not other_rows or other_location not in locations:
                continue
            for other_sku in {r["sku_id"] for r in other_rows}:
                other_profile = active_profiles.get(other_sku)
                if not other_profile:
                    continue
                pair = tuple(sorted((profile["hazard_group"], other_profile["hazard_group"])))
                rule = hazards.get(pair)
                if not rule:
                    reasons.append("missing hazard separation rule")
                    continue
                if rule["prohibit_same_zone"] and location["zone_id"] == locations[other_location]["zone_id"]:
                    reasons.append("hazard zone separation")
                if rule["minimum_clearance_m"] > 0 or rule["barrier_required"]:
                    evidence = separation.get(tuple(sorted((destination, other_location))))
                    if not evidence or evidence["physical_clearance_m"] < rule["minimum_clearance_m"] or rule["barrier_required"] and evidence["barrier_code"] != rule["required_barrier_code"]:
                        reasons.append("hazard clearance/barrier evidence")
        return not reasons, sorted(set(reasons)), face_units

    tasks = [sku for sku in movable if movable[sku]]
    initial_options = {}
    baseline_refills = {}
    for sku in tasks:
        remove_pick_stock(sku)
        initial_options[sku] = sum(feasible(sku, loc)[0] for loc in candidate_ids)
        old = current.get(sku)
        capacity = feasible(sku, old)[2] if old in locations else None
        baseline_refills[sku] = max(0, math.ceil(units[sku] / capacity) - 1) if capacity and units[sku] else None
        restore_pick_stock(sku)
    tasks.sort(key=lambda sku: (initial_options[sku], -picks[sku], -sum(float(row["on_hand_qty"]) for row in movable[sku]), sku))
    recommendations = []
    rejection_by_sku = {}
    for sku in tasks:
        remove_pick_stock(sku)
        choices = []
        rejection = Counter()
        for loc in sorted(candidate_ids):
            okay, reasons, face_units = feasible(sku, loc)
            if not okay:
                LOG.info("Candidate SKU %s -> %s | rejected: %s", sku, loc, ", ".join(reasons))
                rejection.update(reasons)
                continue
            refill = max(0, math.ceil(units[sku] / face_units) - 1) if face_units and units[sku] else None
            # Replenishment is a tie-break, not a measured labor cost.
            choices.append((picks[sku] * distances[loc], refill if refill is not None else float("inf"), distances[loc], loc, refill))
        previous = current.get(sku)
        if choices:
            _, _, _, destination, refill = min(choices)
            occupied[destination].extend(movable[sku])
            decision = "keep" if destination == previous else "move"
            reason = (f"{picks[sku]} {demand_source}; {distances[destination]:.1f} m modeled round trip; "
                      f"{sum(r['handling_unit_count'] for r in movable[sku])} physical packs fit measured capacity and site policies")
            if refill is not None:
                reason += f"; {refill} estimated refill cycles at certified face capacity"
        else:
            destination, refill, decision = None, None, "unassigned"
            restore_pick_stock(sku)
            if not rejection:
                rejection["no safe candidate bin"] = 1
            reason = "No location passes all physical and policy checks. " + ", ".join(f"{name} ({count})" for name, count in rejection.most_common(3))
            rejection_by_sku[sku] = rejection
        old_m = distances.get(previous) if previous else None
        new_m = distances.get(destination) if destination else None
        recommendations.append({"sku_id": sku, "sku_name": skus[sku]["name"], "current_location_id": previous,
                                "proposed_location_id": destination, "decision": decision,
                                "status": "proposed_for_review" if destination else "unassigned",
                                "pick_count": picks[sku], "demand_units": round(units[sku], 3),
                                "demand_velocity_picks_per_day": round(picks[sku] / days, 3) if days else None,
                                "current_round_trip_m": round(old_m, 2) if old_m is not None else None,
                                "proposed_round_trip_m": round(new_m, 2) if new_m is not None else None,
                                "current_estimated_refill_cycles": baseline_refills.get(sku),
                                "estimated_refill_cycles": refill, "reason": reason,
                                "constraint_violations": [] if destination else list(rejection)[:5]})
        LOG.info("Assignment SKU %s | %s -> %s | %s | %s", sku, previous or "none", destination or "unassigned", decision, reason)
    # A prior move can free an occupied bin. Retry skipped SKUs against the final
    # occupancy before declaring them unassigned; use the same hard checks.
    for recommendation in recommendations:
        if recommendation["proposed_location_id"] is not None:
            continue
        sku = recommendation["sku_id"]
        remove_pick_stock(sku)
        choices = []
        for loc in sorted(candidate_ids):
            okay, reasons, face_units = feasible(sku, loc)
            if okay:
                choices.append((picks[sku] * distances[loc], distances[loc], loc, face_units))
            else:
                LOG.info("Retry SKU %s -> %s | rejected: %s", sku, loc, ", ".join(reasons))
        if not choices:
            restore_pick_stock(sku)
            continue
        _, _, destination, face_units = min(choices)
        occupied[destination].extend(movable[sku])
        refill = max(0, math.ceil(units[sku] / face_units) - 1) if face_units and units[sku] else None
        recommendation.update(proposed_location_id=destination,
                              proposed_round_trip_m=round(distances[destination], 2),
                              estimated_refill_cycles=refill,
                              decision="keep" if destination == recommendation["current_location_id"] else "move",
                              status="proposed_for_review", constraint_violations=[],
                              reason=f"Assigned after earlier moves freed this bin; {picks[sku]} {demand_source}; {distances[destination]:.1f} m modeled round trip; physical and policy checks passed.")
        rejection_by_sku.pop(sku, None)
        LOG.info("Retry assignment SKU %s | %s -> %s | %s", sku, recommendation["current_location_id"], destination, recommendation["reason"])
    heuristic_assignments = {r["sku_id"]: r["proposed_location_id"] for r in recommendations}
    heuristic_cost = sum(r["pick_count"] * (r["proposed_round_trip_m"] if r["proposed_round_trip_m"] is not None else r["current_round_trip_m"] or 0) for r in recommendations)
    search_status, search_nodes, search_runtime = "heuristic_only", 0, 0.0
    # Exact branch-and-bound uses the complete physical/policy oracle above. A
    # linear assignment model would miss joint packing and hazard interactions.
    # Larger uploads retain the checked heuristic rather than claiming optimality.
    if len(tasks) <= 8 and len(candidate_ids) <= 20:
        started = time.perf_counter()
        occupied.clear()
        movable_rows = {id(row) for sku in tasks for row in movable[sku]}
        for row in stock:
            if id(row) not in movable_rows:
                occupied[row["location_id"]].append(row)
        fixed = {loc: list(rows) for loc, rows in occupied.items()}
        best = None
        best_plan = None
        exhausted = False
        order = sorted(tasks, key=lambda sku: (initial_options[sku], -picks[sku], sku))
        plan = {}
        # Lexicographic objective: serve the most demand lines, then minimize
        # modeled pick travel, then estimated refill cycles.
        def explore(index, served, travel, refills):
            nonlocal best, best_plan, search_nodes, exhausted
            search_nodes += 1
            if search_nodes > 200000:
                exhausted = True
                return
            if index == len(order):
                key = (-served, travel, refills, tuple(plan[s][0] or "~" for s in order))
                if best is None or key < best:
                    best, best_plan = key, dict(plan)
                return
            sku = order[index]
            remaining = sum(picks[s] for s in order[index:])
            if best is not None and served + remaining < -best[0]:
                return
            options = []
            for loc in sorted(candidate_ids):
                okay, _, face_units = feasible(sku, loc)
                if okay:
                    refill = max(0, math.ceil(units[sku] / face_units) - 1) if face_units and units[sku] else None
                    options.append((picks[sku] * distances[loc], refill if refill is not None else 0, loc, refill))
            for cost, refill_cost, loc, refill in sorted(options):
                occupied[loc].extend(movable[sku])
                plan[sku] = (loc, refill)
                explore(index + 1, served + picks[sku], travel + cost, refills + refill_cost)
                del occupied[loc][-len(movable[sku]):]
                if exhausted:
                    break
            if not exhausted:
                for row in movable[sku]:
                    occupied[row["location_id"]].append(row)
                plan[sku] = (None, None)
                old_cost = picks[sku] * distances.get(current.get(sku), 0)
                explore(index + 1, served, travel + old_cost, refills)
                for row in movable[sku]:
                    occupied[row["location_id"]].remove(row)
        explore(0, 0, 0.0, 0)
        occupied.clear()
        occupied.update({loc: list(rows) for loc, rows in fixed.items()})
        search_runtime = round(time.perf_counter() - started, 4)
        if best_plan is not None and not exhausted:
            search_status = "optimal"
            rejection_by_sku.clear()
            for sku in order:
                destination, refill = best_plan[sku]
                if destination:
                    occupied[destination].extend(movable[sku])
                else:
                    restore_pick_stock(sku)
            for recommendation in recommendations:
                sku = recommendation["sku_id"]
                destination, refill = best_plan[sku]
                if destination:
                    recommendation.update(proposed_location_id=destination, proposed_round_trip_m=round(distances[destination], 2),
                                          estimated_refill_cycles=refill, decision="keep" if destination == recommendation["current_location_id"] else "move",
                                          status="proposed_for_review", constraint_violations=[],
                                          reason=f"Globally optimized among safe assignments; {picks[sku]} {demand_source}; {distances[destination]:.1f} m modeled round trip; site capacity and policies passed.")
                else:
                    remove_pick_stock(sku)
                    rejection = Counter()
                    for loc in sorted(candidate_ids):
                        okay, reasons, _ = feasible(sku, loc)
                        if not okay:
                            LOG.info("Final candidate SKU %s -> %s | rejected: %s", sku, loc, ", ".join(reasons))
                            rejection.update(reasons)
                    restore_pick_stock(sku)
                    if not rejection:
                        rejection["global capacity/compatibility conflict"] = 1
                    rejection_by_sku[sku] = rejection
                    recommendation.update(proposed_location_id=None, proposed_round_trip_m=None, estimated_refill_cycles=None,
                                          decision="unassigned", status="unassigned", constraint_violations=list(rejection)[:5],
                                          reason="No safe placement in the global assignment. " + ", ".join(f"{name} ({count})" for name, count in rejection.most_common(3)))
        else:
            search_status = "node_limit_heuristic" if exhausted else "heuristic_only"
            occupied.clear()
            occupied.update({loc: list(rows) for loc, rows in fixed.items()})
            for sku, loc in heuristic_assignments.items():
                if loc:
                    occupied[loc].extend(movable[sku])
                else:
                    restore_pick_stock(sku)
    LOG.info("Global search %s | nodes %d | runtime %.4f s | heuristic cost %.2f", search_status, search_nodes, search_runtime, heuristic_cost)
    violations = Counter()
    for rejection in rejection_by_sku.values():
        violations.update(rejection)
    for sku in sorted(set(picks) - set(tasks)):
        recommendations.append({"sku_id": sku, "sku_name": skus[sku]["name"], "current_location_id": current.get(sku),
                                "proposed_location_id": None, "decision": "unassigned", "status": "no_movable_pick_stock",
                                "pick_count": picks[sku], "demand_units": round(units[sku], 3),
                                "demand_velocity_picks_per_day": round(picks[sku] / days, 3) if days else None,
                                "current_round_trip_m": distances.get(current.get(sku)), "proposed_round_trip_m": None,
                                "current_estimated_refill_cycles": None, "estimated_refill_cycles": None, "reason": "No available, unheld stock in an assigned pick face; no physical move was inferred.",
                                "constraint_violations": ["no movable pick stock"]})
    recommendations.sort(key=lambda r: (-r["pick_count"], r["sku_id"]))
    movement_lines = []
    for recommendation in recommendations:
        destination = recommendation["proposed_location_id"]
        if not destination:
            continue
        for row in movable[recommendation["sku_id"]]:
            if row["location_id"] == destination:
                continue
            movement_lines.append({"sku_id": row["sku_id"], "source_location_id": row["location_id"],
                                   "proposed_location_id": destination, "packaging_profile_id": row["packaging_profile_id"],
                                   "lot_id": row["lot_id"], "handling_unit_count": row["handling_unit_count"],
                                   "on_hand_qty": float(row["on_hand_qty"]), "base_uom": skus[row["sku_id"]]["base_uom"],
                                   "stock_status": row["stock_status"], "action": "proposed_move_for_review"})
    baseline = sum(r["pick_count"] * r["current_round_trip_m"] for r in recommendations if r["current_round_trip_m"] is not None)
    proposed = sum(r["pick_count"] * r["proposed_round_trip_m"] for r in recommendations if r["proposed_round_trip_m"] is not None)
    comparable = [r for r in recommendations if r["current_round_trip_m"] is not None and r["proposed_round_trip_m"] is not None]
    baseline_comparable = sum(r["pick_count"] * r["current_round_trip_m"] for r in comparable)
    proposed_comparable = sum(r["pick_count"] * r["proposed_round_trip_m"] for r in comparable)
    improvement = round(100 * (1 - proposed_comparable / baseline_comparable), 2) if baseline_comparable else None
    scored_proposal = sum(r["pick_count"] * (r["proposed_round_trip_m"] if r["proposed_round_trip_m"] is not None else r["current_round_trip_m"]) for r in recommendations if r["current_round_trip_m"] is not None)
    score = round(100 * (1 - scored_proposal / baseline), 2) if baseline else None
    # An optimistic ceiling: every pick uses the shortest eligible round trip.
    # Ignoring SKU compatibility makes this an upper bound, never an accuracy claim.
    min_route = min((distances[loc] for loc in candidate_ids), default=None)
    score_ceiling = round(100 * (1 - sum(r["pick_count"] * min(r["current_round_trip_m"], min_route)
                                         for r in recommendations if r["current_round_trip_m"] is not None) / baseline), 2) if baseline and min_route is not None else None
    usable = sum(float(locations[loc]["usable_volume_cm3"]) for loc in candidate_ids)
    initial_volume = sum(float(packages[row["packaging_profile_id"]]["outer_length_cm"] * packages[row["packaging_profile_id"]]["outer_width_cm"] * packages[row["packaging_profile_id"]]["outer_height_cm"] * row["handling_unit_count"]) for row in stock if row["location_id"] in candidate_ids)
    occupied_volume = sum(float(packages[row["packaging_profile_id"]]["outer_length_cm"] * packages[row["packaging_profile_id"]]["outer_width_cm"] * packages[row["packaging_profile_id"]]["outer_height_cm"] * row["handling_unit_count"]) for loc in candidate_ids for row in occupied[loc])
    demanded = [r for r in recommendations if r["pick_count"] > 0]
    baseline_refill_total = sum(r["current_estimated_refill_cycles"] for r in demanded) if all(r["current_estimated_refill_cycles"] is not None for r in demanded) else None
    proposed_refill_total = sum(r["estimated_refill_cycles"] for r in demanded) if all(r["estimated_refill_cycles"] is not None for r in demanded) else None
    result = {"dataset_id": imported.manifest["dataset_id"], "warehouse_id": warehouse, "warehouse_name": warehouse_name,
              "snapshot_at": snapshot.isoformat(), "demand_source": demand_source,
              "status": "provisional_analysis", "assumptions": ["A checked global branch-and-bound assignment is used for at most 8 movable SKUs and 20 candidate bins; larger or node-limited cases use the constrained heuristic.", "Available unreserved pick stock may move; reserve, held and non-pickable stock stays in place.", "Modeled travel is a depot-to-bin outbound and return path on the supplied aisle graph; batch routing is not simulated.", "The score is percent change in modeled pick travel against the current layout; unassigned picks receive no savings credit.", "Replenishment cycles are capacity-based estimates, not observed tasks or replenishment travel.", "Uploaded lineage and site approvals have not been independently verified."],
              "statistics": {"skus": len(skus), "locations": len(locations), "inventory_rows": len(stock), "demand_lines": sum(picks.values()), "candidate_locations": len(candidate_ids)},
              "metrics": {"assigned_skus": sum(r["proposed_location_id"] is not None for r in recommendations),
                          "unassigned_skus": sum(r["proposed_location_id"] is None for r in recommendations),
                          "baseline_pick_distance_m": round(baseline, 2), "proposed_pick_distance_m": round(proposed, 2),
                          "comparable_baseline_m": round(baseline_comparable, 2), "comparable_proposed_m": round(proposed_comparable, 2),
                          "estimated_distance_improvement_pct": improvement, "optimization_score": score,
                          "theoretical_travel_score_ceiling_pct": score_ceiling,
                          "unserved_pick_count": sum(r["pick_count"] for r in recommendations if r["proposed_location_id"] is None),
                          "baseline_utilization_pct": round(100 * initial_volume / usable, 2) if usable else None,
                          "proposed_utilization_pct": round(100 * occupied_volume / usable, 2) if usable else None,
                          "baseline_replenishment_cycles": baseline_refill_total,
                          "estimated_replenishment_cycles": proposed_refill_total,
                          "constraint_violations": dict(violations)},
              "solver": {"status": search_status, "search_nodes": search_nodes, "runtime_s": search_runtime,
                         "heuristic_modeled_travel_m": round(heuristic_cost, 2),
                         "objective_gap_pct": 0.0 if search_status == "optimal" else None},
              "validation": {"warnings": imported.report["warnings"], "incidents": imported.report["incidents"],
                             "blocked_locations": imported.report["warehouses"][warehouse]["blocked_locations"]},
              "recommendations": recommendations, "movement_lines": movement_lines}
    if any(warning["code"] == "post_snapshot_demand" for warning in imported.report["warnings"]):
        result["assumptions"].append("Orders later than the physical snapshot were used only as a post-snapshot demand window for scenario analysis; align the demand window with a current inventory snapshot before operational use.")
    if any(warning["code"] == "future_snapshot_preview" for warning in imported.report["warnings"]):
        result["assumptions"].append("The supplied snapshot timestamp is future-dated; this is a scenario preview, not a current warehouse plan.")
    LOG.info("Dataset %s | warehouse %s | snapshot %s | rows %s", result["dataset_id"], warehouse, snapshot, result["statistics"])
    LOG.info("Candidate bins %d | movable SKUs %d | unassigned %d", len(candidate_ids), len(tasks), result["metrics"]["unassigned_skus"])
    LOG.info("Optimization metrics %s", result["metrics"])
    LOG.info("Constraint violations %s", dict(violations))
    LOG.info("Final optimization score %s", score)
    LOG.info("Optimistic travel score ceiling %s%% | shortest eligible round trip %s m | ignores SKU compatibility", score_ceiling, min_route)
    return result
