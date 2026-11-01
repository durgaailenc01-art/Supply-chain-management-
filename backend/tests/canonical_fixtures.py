"""Small, wholly synthetic canonical packages for importer tests."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from backend.canonical_schema import FULL_REQUIRED, TABLES

SNAPSHOT = "2026-01-31T12:00:00Z"
EXPORTED = "2026-01-31T12:05:00Z"
START = "2026-01-01T00:00:00Z"


def record(table: str, **values: str) -> dict[str, str]:
    row = {column.name: "" for column in TABLES[table]}
    row.update(values)
    unknown = set(row) - {column.name for column in TABLES[table]}
    if unknown:
        raise ValueError(f"unknown {table} columns: {unknown}")
    return row


def shared_bin_rows() -> dict[str, list[dict[str, str]]]:
    rows = {name: [] for name in FULL_REQUIRED}
    rows.update({
        "warehouses": [record("warehouses", warehouse_id="WH1", name="Fixture warehouse", timezone="UTC", status="active", default_pick_node_id="N0")],
        "zones": [record("zones", zone_id="Z1", warehouse_id="WH1", name="Ambient", zone_type="ambient", status="active")],
        "locations": [record("locations", location_id="L1", warehouse_id="WH1", zone_id="Z1", location_type="pick", status="active", blocked="false", usable_length_cm="100", usable_width_cm="100", usable_height_cm="100", usable_volume_cm3="1000000", max_total_weight_kg="1000", max_distinct_skus="2", allow_mixed_lots="true", max_lots_per_sku="2", temperature_class="ambient", operating_temp_min_c="0", operating_temp_max_c="30")],
        "skus": [
            record("skus", sku_id="S1", name="Synthetic item one", status="active", is_stock_item="true", base_uom="ea", quantity_precision="0", lot_tracking="lot"),
            record("skus", sku_id="S2", name="Synthetic item two", status="active", is_stock_item="true", base_uom="ea", quantity_precision="0", lot_tracking="none"),
        ],
        "sku_profiles": [
            record("sku_profiles", profile_id="SP1", sku_id="S1", effective_from="2025-01-01T00:00:00Z", temperature_class="ambient", storage_temp_min_c="0", storage_temp_max_c="30", hazard_group="none", co_storage_group="food"),
            record("sku_profiles", profile_id="SP2", sku_id="S2", effective_from="2025-01-01T00:00:00Z", temperature_class="ambient", storage_temp_min_c="0", storage_temp_max_c="30", hazard_group="none", co_storage_group="food"),
        ],
        "sku_packaging_profiles": [
            record("sku_packaging_profiles", packaging_profile_id="PK1", sku_id="S1", packaging_code="base", base_units_per_pack="1", outer_length_cm="10", outer_width_cm="10", outer_height_cm="10", gross_weight_kg="2", orientation_policy="fixed", stackable="false", effective_from="2025-01-01T00:00:00Z"),
            record("sku_packaging_profiles", packaging_profile_id="PK2", sku_id="S2", packaging_code="base", base_units_per_pack="1", outer_length_cm="10", outer_width_cm="10", outer_height_cm="10", gross_weight_kg="2", orientation_policy="fixed", stackable="false", effective_from="2025-01-01T00:00:00Z"),
        ],
        "sku_lots": [record("sku_lots", lot_record_id="LR1", sku_id="S1", lot_id="LOT1", disposition_at_snapshot="released")],
        "inventory_by_location": [
            record("inventory_by_location", inventory_record_id="I1", warehouse_id="WH1", location_id="L1", sku_id="S1", lot_id="LOT1", packaging_profile_id="PK1", handling_unit_count="2", pack_state="sealed", stock_status="available", on_hand_qty="2", reserved_qty="0", hold_qty="0", snapshot_at=SNAPSHOT),
            record("inventory_by_location", inventory_record_id="I2", warehouse_id="WH1", location_id="L1", sku_id="S2", packaging_profile_id="PK2", handling_unit_count="2", pack_state="sealed", stock_status="available", on_hand_qty="2", reserved_qty="0", hold_qty="0", snapshot_at=SNAPSHOT),
        ],
        "slot_assignments": [
            record("slot_assignments", slot_assignment_id="A1", warehouse_id="WH1", sku_id="S1", location_id="L1", role="pick", status="active", snapshot_at=SNAPSHOT),
            record("slot_assignments", slot_assignment_id="A2", warehouse_id="WH1", sku_id="S2", location_id="L1", role="pick", status="active", snapshot_at=SNAPSHOT),
        ],
        "aisle_nodes": [
            record("aisle_nodes", node_id="N0", warehouse_id="WH1", node_type="depot", floor_level="0", status="active"),
            record("aisle_nodes", node_id="N1", warehouse_id="WH1", node_type="location_access", floor_level="0", status="active"),
        ],
        "aisle_edges": [record("aisle_edges", edge_id="E1", warehouse_id="WH1", from_node_id="N0", to_node_id="N1", length_m="5", bidirectional="true", travel_mode="picker", status="active")],
        "location_access": [record("location_access", access_id="AC1", warehouse_id="WH1", location_id="L1", node_id="N1", walk_offset_m="1", travel_mode="picker", status="active")],
        "location_hazard_permissions": [record("location_hazard_permissions", permission_id="HP1", warehouse_id="WH1", location_id="L1", hazard_group="none")],
        "hazard_segregation_rules": [record("hazard_segregation_rules", rule_id="HR1", warehouse_id="WH1", hazard_group_a="none", hazard_group_b="none", allow_same_location="true", minimum_clearance_m="0", prohibit_same_zone="false", barrier_required="false", approval_ref="fixture-policy", effective_from="2025-01-01T00:00:00Z")],
        "co_storage_rules": [record("co_storage_rules", co_storage_rule_id="CS1", warehouse_id="WH1", group_a="food", group_b="food", allowed="true", approval_ref="fixture-policy", effective_from="2025-01-01T00:00:00Z")],
        "shared_bin_plans": [record("shared_bin_plans", plan_id="BP1", warehouse_id="WH1", location_id="L1", certified_at="2026-01-30T00:00:00Z", approval_ref="fixture-plan")],
        "shared_bin_plan_items": [
            record("shared_bin_plan_items", plan_item_id="BPI1", plan_id="BP1", packaging_profile_id="PK1", max_handling_unit_count="4"),
            record("shared_bin_plan_items", plan_item_id="BPI2", plan_id="BP1", packaging_profile_id="PK2", max_handling_unit_count="4"),
        ],
        "orders": [record("orders", order_id="O1", warehouse_id="WH1", ordered_at="2026-01-02T00:00:00Z", status="fulfilled")],
        "order_lines": [record("order_lines", order_line_id="OL1", order_id="O1", sku_id="S1", line_number="1", ordered_qty="2", fulfilled_qty="2", status="fulfilled")],
        "pick_events": [
            record("pick_events", pick_event_id="PE1", warehouse_id="WH1", order_line_id="OL1", sku_id="S1", location_id="L1", lot_id="LOT1", picked_qty="1", event_at="2026-01-03T01:00:00Z", status="completed"),
            record("pick_events", pick_event_id="PE2", warehouse_id="WH1", order_line_id="OL1", sku_id="S1", location_id="L1", lot_id="LOT1", picked_qty="1", event_at="2026-01-03T02:00:00Z", status="completed"),
        ],
    })
    return rows


def write_package(path: Path, rows: dict[str, list[dict[str, str]]], *, mode: str = "full_snapshot") -> None:
    path.mkdir(parents=True, exist_ok=True)
    files = {}
    for table, records in sorted(rows.items()):
        filename = f"{table}.csv"
        with (path / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[column.name for column in TABLES[table]], lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        files[filename] = {
            "filename": filename,
            "rows": len(records),
            "sha256": hashlib.sha256((path / filename).read_bytes()).hexdigest(),
            "source_extracts": [{"source_system": "synthetic-wms", "source_extract_id": f"fixture-{table}", "captured_at": SNAPSHOT}],
        }
    manifest = {
        "schema_version": "1.0", "dataset_id": "synthetic-fixture", "tenant_id": "test-tenant",
        "source_system": "synthetic-wms", "mapping_version": "fixture-v1",
        "storage_policy_version": "fixture-policy-v1" if mode == "full_snapshot" else "", "import_kind": mode,
        "snapshot_at": SNAPSHOT, "exported_at": EXPORTED,
        "consistency_method": "transactional" if mode == "full_snapshot" else "history_extract",
        "history_from": START, "history_to": SNAPSHOT,
        "warehouse_ids": [row["warehouse_id"] for row in rows["warehouses"]], "source_timezones": {"synthetic-wms": "UTC"},
        "files": files,
    }
    manifest["consistency_token" if mode == "full_snapshot" else "history_extract_ref"] = "fixture-token"
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
