"""Generate a reproducible, wholly synthetic canonical upload package.

The certificate values are intentionally conservative examples, not site approvals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from backend.canonical_schema import TABLES

SNAPSHOT = "2026-10-31T00:00:00Z"
HISTORY_FROM = "2026-10-01T00:00:00Z"
EXPORTED = "2026-10-31T00:05:00Z"


def generate(output: Path, warehouses: int = 20, locations: int = 10000, skus: int = 5000) -> None:
    if warehouses < 1 or locations < skus * 2 or skus < warehouses or locations % warehouses or skus % warehouses:
        raise ValueError("Use positive counts with locations >= 2 × SKUs and both totals divisible by warehouses")
    output.mkdir(parents=True, exist_ok=True)
    handles = {}
    writers = {}
    counts = {name: 0 for name in TABLES}
    for name, columns in TABLES.items():
        handle = (output / f"{name}.csv").open("w", newline="", encoding="utf-8")
        handles[name] = handle
        writer = csv.DictWriter(handle, fieldnames=[column.name for column in columns], lineterminator="\n")
        writer.writeheader()
        writers[name] = writer

    def add(table: str, **fields: str) -> None:
        writers[table].writerow(fields)
        counts[table] += 1

    per_wh_locations = locations // warehouses
    per_wh_skus = skus // warehouses
    try:
        for w in range(1, warehouses + 1):
            wh = f"WH-{w:02d}"
            zone = f"ZONE-{w:02d}"
            depot = f"DEPOT-{w:02d}"
            add("warehouses", warehouse_id=wh, name=f"Synthetic Warehouse {w:02d}", timezone="UTC", status="active", default_pick_node_id=depot)
            add("zones", zone_id=zone, warehouse_id=wh, name="Ambient picking", zone_type="ambient", status="active")
            add("aisle_nodes", node_id=depot, warehouse_id=wh, node_type="depot", floor_level="0", status="active")
            for n in range(1, per_wh_locations + 1):
                loc = f"BIN-{w:02d}-{n:04d}"
                node = f"NODE-{w:02d}-{n:04d}"
                add("locations", location_id=loc, warehouse_id=wh, zone_id=zone, location_type="pick", status="active", blocked="false", usable_length_cm="100", usable_width_cm="100", usable_height_cm="100", usable_volume_cm3="1000000", max_total_weight_kg="100", max_distinct_skus="1", allow_mixed_lots="false", max_lots_per_sku="1", temperature_class="ambient", operating_temp_min_c="0", operating_temp_max_c="30", aisle_label=f"A{(n-1)//25+1:02d}", bay_label=f"{(n-1)%25+1:02d}")
                add("aisle_nodes", node_id=node, warehouse_id=wh, node_type="location_access", floor_level="0", status="active")
                add("aisle_edges", edge_id=f"EDGE-{w:02d}-{n:04d}", warehouse_id=wh, from_node_id=depot, to_node_id=node, length_m=str(5 + (n - 1) % 25 * 2 + (n - 1) // 25 * 3), bidirectional="true", travel_mode="picker", status="active")
                add("location_access", access_id=f"ACCESS-{w:02d}-{n:04d}", warehouse_id=wh, location_id=loc, node_id=node, walk_offset_m="1", travel_mode="picker", status="active")
                add("location_hazard_permissions", permission_id=f"PERM-{w:02d}-{n:04d}", warehouse_id=wh, location_id=loc, hazard_group="none")
            add("hazard_segregation_rules", rule_id=f"HAZ-{w:02d}", warehouse_id=wh, hazard_group_a="none", hazard_group_b="none", allow_same_location="true", minimum_clearance_m="0", prohibit_same_zone="false", barrier_required="false", approval_ref="synthetic-policy", effective_from=HISTORY_FROM)
            for s in range(1, per_wh_skus + 1):
                sku = f"SKU-{w:02d}-{s:04d}"
                pack = f"PACK-{w:02d}-{s:04d}"
                near = f"BIN-{w:02d}-{s:04d}"
                far = f"BIN-{w:02d}-{per_wh_skus+s:04d}"
                # Deliberately include inefficient current placements so a test
                # run exercises both safe moves and justified keep decisions.
                current, alternate = (far, near) if s % 3 == 0 else (near, far)
                add("skus", sku_id=sku, name=f"Synthetic product {w:02d}-{s:04d}", status="active", is_stock_item="true", base_uom="ea", quantity_precision="0", lot_tracking="none")
                add("sku_profiles", profile_id=f"PROFILE-{w:02d}-{s:04d}", sku_id=sku, effective_from=HISTORY_FROM, temperature_class="ambient", storage_temp_min_c="0", storage_temp_max_c="30", hazard_group="none", co_storage_group="general")
                add("sku_packaging_profiles", packaging_profile_id=pack, sku_id=sku, packaging_code="case", base_units_per_pack="1", outer_length_cm="10", outer_width_cm="10", outer_height_cm="10", gross_weight_kg="1", orientation_policy="fixed", stackable="false", effective_from=HISTORY_FROM)
                for index, loc in enumerate((current, alternate), 1):
                    add("location_pack_capacities", capacity_id=f"CAP-{w:02d}-{s:04d}-{index}", warehouse_id=wh, location_id=loc, packaging_profile_id=pack, max_handling_unit_count="10", certified_at="2026-10-30T00:00:00Z", basis="site_test", approval_ref="SYNTHETIC-ONLY")
                add("inventory_by_location", inventory_record_id=f"INV-{w:02d}-{s:04d}", warehouse_id=wh, location_id=current, sku_id=sku, packaging_profile_id=pack, handling_unit_count="5", pack_state="sealed", stock_status="available", on_hand_qty="5", reserved_qty="0", hold_qty="0", snapshot_at=SNAPSHOT)
                add("slot_assignments", slot_assignment_id=f"SLOT-{w:02d}-{s:04d}", warehouse_id=wh, sku_id=sku, location_id=current, role="pick", status="active", snapshot_at=SNAPSHOT)
                order = f"ORDER-{w:02d}-{s:04d}"
                add("orders", order_id=order, warehouse_id=wh, ordered_at="2026-10-15T12:00:00Z", status="fulfilled")
                add("order_lines", order_line_id=f"LINE-{w:02d}-{s:04d}", order_id=order, sku_id=sku, line_number="1", ordered_qty=str(1 + s % 10), fulfilled_qty=str(1 + s % 10), status="fulfilled")
    finally:
        for handle in handles.values():
            handle.close()

    files = {}
    for name, count in counts.items():
        filename = f"{name}.csv"
        files[filename] = {"filename": filename, "rows": count, "sha256": hashlib.sha256((output / filename).read_bytes()).hexdigest(), "source_extracts": [{"source_system": "synthetic-wms", "source_extract_id": f"generated-{name}", "captured_at": SNAPSHOT}]}
    manifest = {"schema_version": "1.0", "dataset_id": f"synthetic-{warehouses}w-{locations}l-{skus}s", "tenant_id": "synthetic-sample", "source_system": "synthetic-wms", "mapping_version": "generator-v1", "storage_policy_version": "synthetic-policy-v1", "import_kind": "full_snapshot", "snapshot_at": SNAPSHOT, "exported_at": EXPORTED, "consistency_method": "transactional", "consistency_token": "synthetic-generation", "history_from": HISTORY_FROM, "history_to": SNAPSHOT, "warehouse_ids": [f"WH-{w:02d}" for w in range(1, warehouses + 1)], "source_timezones": {"synthetic-wms": "UTC"}, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.output)
