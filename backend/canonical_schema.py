"""Declarative columns for the proposed WMS-neutral CSV contract (schema 1.0).

``!`` marks a value required on every row. Conditional requirements are checked
with cross-field/domain rules by canonical_import.py. Every listed column must
appear in a canonical CSV, including optional columns whose values are blank.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    required: bool = False


_DEFINITIONS = {
    "warehouses": "warehouse_id:id! name:text! timezone:text! status:enum! default_pick_node_id:id operating_notes:text",
    "zones": "zone_id:id! warehouse_id:id! name:text! zone_type:enum! status:enum! equipment_code:text",
    "locations": "location_id:id! warehouse_id:id! zone_id:id! location_type:enum! status:enum! blocked:bool! usable_length_cm:decimal usable_width_cm:decimal usable_height_cm:decimal usable_volume_cm3:decimal max_total_weight_kg:decimal max_distinct_skus:int allow_mixed_lots:bool max_lots_per_sku:int temperature_class:enum! operating_temp_min_c:decimal operating_temp_max_c:decimal pick_height_cm:decimal aisle_label:text bay_label:text level_label:text equipment_code:text",
    "historical_location_refs": "location_id:id! warehouse_id:id! retired_at:timestamp",
    "skus": "sku_id:id! name:text! category_code:text status:enum! is_stock_item:bool! base_uom:enum! quantity_precision:int! lot_tracking:enum!",
    "sku_profiles": "profile_id:id! sku_id:id! warehouse_id:id effective_from:timestamp effective_to:timestamp unit_length_cm:decimal unit_width_cm:decimal unit_height_cm:decimal unit_weight_kg:decimal orientation_policy:enum stackable:bool max_stack_units:int temperature_class:enum! storage_temp_min_c:decimal! storage_temp_max_c:decimal! hazard_group:text! co_storage_group:text!",
    "sku_uom_conversions": "conversion_id:id! sku_id:id! warehouse_id:id source_system:text! source_uom:text! base_units_per_source_unit:decimal! effective_from:timestamp! effective_to:timestamp",
    "sku_packaging_profiles": "packaging_profile_id:id! sku_id:id! warehouse_id:id packaging_code:text! base_units_per_pack:decimal! outer_length_cm:decimal! outer_width_cm:decimal! outer_height_cm:decimal! gross_weight_kg:decimal! orientation_policy:enum! stackable:bool! max_stack_layers:int max_supported_top_load_kg:decimal effective_from:timestamp! effective_to:timestamp",
    "sku_lots": "lot_record_id:id! sku_id:id! lot_id:text! expiry_at:timestamp expiry_on:date received_at:timestamp disposition_at_snapshot:enum",
    "inventory_by_location": "inventory_record_id:id! warehouse_id:id! location_id:id! sku_id:id! lot_id:text packaging_profile_id:id! handling_unit_count:int! pack_state:enum! stock_status:enum! on_hand_qty:decimal! reserved_qty:decimal! hold_qty:decimal! snapshot_at:timestamp!",
    "location_pack_capacities": "capacity_id:id! warehouse_id:id! location_id:id! packaging_profile_id:id! max_handling_unit_count:int! certified_at:timestamp! valid_until:timestamp basis:enum! approval_ref:text!",
    "slot_assignments": "slot_assignment_id:id! warehouse_id:id! sku_id:id! location_id:id! role:enum! status:enum! snapshot_at:timestamp! valid_from:timestamp valid_to:timestamp last_changed_at:timestamp min_pick_qty:decimal max_pick_qty:decimal pick_priority:int",
    "co_storage_rules": "co_storage_rule_id:id! warehouse_id:id! group_a:text! group_b:text! allowed:bool! approval_ref:text! effective_from:timestamp! effective_to:timestamp",
    "shared_bin_plans": "plan_id:id! warehouse_id:id! location_id:id! certified_at:timestamp! valid_until:timestamp approval_ref:text!",
    "shared_bin_plan_items": "plan_item_id:id! plan_id:id! packaging_profile_id:id! max_handling_unit_count:int!",
    "orders": "order_id:id! warehouse_id:id! ordered_at:timestamp ordered_on:date status:enum! due_at:timestamp channel_code:text wave_id:id",
    "order_lines": "order_line_id:id! order_id:id! sku_id:id! line_number:int ordered_qty:decimal! fulfilled_qty:decimal status:enum! wave_id:id",
    "pick_events": "pick_event_id:id! warehouse_id:id! order_line_id:id sku_id:id! location_id:id lot_id:text picked_qty:decimal! event_at:timestamp! started_at:timestamp status:enum! wave_id:id route_id:id route_sequence:int picker_pseudonym:text distance_m:decimal distance_method:enum",
    "replenishment_events": "replenishment_event_id:id! warehouse_id:id! sku_id:id! lot_id:text source_location_id:id source_node_id:id destination_location_id:id moved_qty:decimal! event_at:timestamp! requested_at:timestamp started_at:timestamp completed_at:timestamp status:enum! method_code:text worker_pseudonym:text distance_m:decimal distance_method:enum",
    "aisle_nodes": "node_id:id! warehouse_id:id! node_type:enum! x_m:decimal y_m:decimal floor_level:int! status:enum!",
    "aisle_edges": "edge_id:id! warehouse_id:id! from_node_id:id! to_node_id:id! length_m:decimal! bidirectional:bool! travel_mode:enum! status:enum! level_change:int",
    "location_access": "access_id:id! warehouse_id:id! location_id:id! node_id:id! walk_offset_m:decimal! travel_mode:enum! status:enum!",
    "location_hazard_permissions": "permission_id:id! warehouse_id:id! location_id:id! hazard_group:text!",
    "hazard_segregation_rules": "rule_id:id! warehouse_id:id! hazard_group_a:text! hazard_group_b:text! allow_same_location:bool! minimum_clearance_m:decimal! prohibit_same_zone:bool! barrier_required:bool! required_barrier_code:text approval_ref:text! effective_from:timestamp! effective_to:timestamp",
    "hazard_separation_evidence": "evidence_id:id! warehouse_id:id! location_a_id:id! location_b_id:id! physical_clearance_m:decimal! barrier_code:text surveyed_at:timestamp! valid_until:timestamp approval_ref:text!",
}


def _columns(definition: str) -> tuple[Column, ...]:
    result = []
    for token in definition.split():
        name, kind = token.split(":")
        result.append(Column(name, kind.rstrip("!"), kind.endswith("!")))
    return tuple(result)


TABLES: dict[str, tuple[Column, ...]] = {name: _columns(fields) for name, fields in _DEFINITIONS.items()}
# Previously exported Slotwise packages used access_offset_m. Normalize that
# historical header at the read boundary; all validated rows use the contract name.
LEGACY_COLUMN_ALIASES = {"location_access": {"access_offset_m": "walk_offset_m"}}
PRIMARY_KEYS = {
    "warehouses": "warehouse_id", "zones": "zone_id", "locations": "location_id",
    "historical_location_refs": "location_id", "skus": "sku_id", "sku_profiles": "profile_id",
    "sku_uom_conversions": "conversion_id", "sku_packaging_profiles": "packaging_profile_id",
    "sku_lots": "lot_record_id", "inventory_by_location": "inventory_record_id",
    "location_pack_capacities": "capacity_id", "slot_assignments": "slot_assignment_id",
    "co_storage_rules": "co_storage_rule_id", "shared_bin_plans": "plan_id",
    "shared_bin_plan_items": "plan_item_id", "orders": "order_id",
    "order_lines": "order_line_id", "pick_events": "pick_event_id",
    "replenishment_events": "replenishment_event_id", "aisle_nodes": "node_id",
    "aisle_edges": "edge_id", "location_access": "access_id",
    "location_hazard_permissions": "permission_id", "hazard_segregation_rules": "rule_id",
    "hazard_separation_evidence": "evidence_id",
}

ENUMS = {
    ("warehouses", "status"): {"active", "inactive"},
    ("zones", "zone_type"): {"ambient", "chilled", "frozen", "hazmat", "bulk", "other"},
    ("zones", "status"): {"active", "inactive"},
    ("locations", "location_type"): {"pick", "reserve", "both", "staging"},
    ("locations", "status"): {"active", "inactive"},
    ("locations", "temperature_class"): {"ambient", "chilled", "frozen", "controlled"},
    ("skus", "status"): {"active", "inactive", "discontinued"},
    ("skus", "base_uom"): {"ea", "case", "pallet", "kg", "l"},
    ("skus", "lot_tracking"): {"none", "lot", "expiry", "serial"},
    ("sku_profiles", "orientation_policy"): {"fixed", "rotate_horizontal", "rotate_any"},
    ("sku_profiles", "temperature_class"): {"ambient", "chilled", "frozen", "controlled"},
    ("sku_packaging_profiles", "orientation_policy"): {"fixed", "rotate_horizontal", "rotate_any"},
    ("sku_lots", "disposition_at_snapshot"): {"released", "quarantine", "recalled", "expired"},
    ("inventory_by_location", "pack_state"): {"sealed", "opened", "loose"},
    ("inventory_by_location", "stock_status"): {"available", "quarantine", "damaged", "expired", "other_nonpickable"},
    ("location_pack_capacities", "basis"): {"site_test", "vendor_spec", "engineering"},
    ("slot_assignments", "role"): {"pick", "reserve", "both"},
    ("slot_assignments", "status"): {"active", "inactive"},
    ("orders", "status"): {"open", "allocated", "partially_fulfilled", "fulfilled", "cancelled", "other"},
    ("order_lines", "status"): {"open", "allocated", "partially_fulfilled", "fulfilled", "cancelled", "other"},
    ("pick_events", "status"): {"completed", "short", "failed", "cancelled"},
    ("pick_events", "distance_method"): {"measured", "telemetry", "wms_estimate"},
    ("replenishment_events", "status"): {"requested", "in_progress", "completed", "cancelled", "failed"},
    ("replenishment_events", "distance_method"): {"measured", "telemetry", "wms_estimate"},
    ("aisle_nodes", "node_type"): {"depot", "packing", "intersection", "aisle_end", "location_access", "staging"},
    ("aisle_nodes", "status"): {"active", "inactive"},
    ("aisle_edges", "travel_mode"): {"picker", "forklift", "both"},
    ("aisle_edges", "status"): {"active", "blocked", "inactive"},
    ("location_access", "travel_mode"): {"picker", "forklift", "both"},
    ("location_access", "status"): {"active", "inactive"},
}

FULL_REQUIRED = {
    "warehouses", "zones", "locations", "skus", "sku_profiles",
    "sku_packaging_profiles", "inventory_by_location", "slot_assignments",
    "aisle_nodes", "aisle_edges", "location_access", "location_hazard_permissions",
    "hazard_segregation_rules",
}
DEMAND_REQUIRED = {"warehouses", "skus", "orders", "order_lines"}
