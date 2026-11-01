# Data dictionary

CSV files use UTF-8, headers in snake case, ISO-8601 UTC timestamps, empty text for optional values, and decimal notation for numeric values. IDs are immutable. `PK` means unique primary key; `FK` names the parent table. Unless noted, numeric fields must be nonnegative. `status` values describe a record's lifecycle. The generator's synthetic names have no real-world provenance.

## Warehouse (`warehouses.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| warehouse_id | string | Yes | `WH-001` pattern | PK; immutable |
| name | string | Yes | nonempty display name | Not unique |
| status | enum | Yes | `active`, `inactive` | Valid enum |
| labor_capacity_per_shift | integer | Yes | tasks per shift, > 0 | Not unique |

## Zone (`zones.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| zone_id | string | Yes | `ZON-WH-001-01` pattern | PK |
| warehouse_id | string | Yes | warehouse ID | FK → Warehouse |
| name | string | Yes | nonempty name | Not unique |
| zone_type | enum | Yes | `ambient`, `bulk`, `chilled`, `hazmat` | Valid enum |
| status | enum | Yes | `active`, `inactive` | Valid enum |

## Location (`locations.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| location_id | string | Yes | `LOC-<zone-id>-000001` | PK |
| zone_id | string | Yes | zone ID | FK → Zone |
| warehouse_id | string | Yes | warehouse ID | FK → Warehouse; equals Zone warehouse |
| aisle | integer | Yes | 1-based aisle coordinate | > 0 |
| bay | integer | Yes | 1-based bay coordinate | > 0 |
| length_cm | integer | Yes | centimetres | > 0 |
| width_cm | integer | Yes | centimetres | > 0 |
| height_cm | integer | Yes | centimetres | > 0 |
| capacity_cm3 | integer | Yes | cubic centimetres | = length × width × height |
| max_weight_kg | integer | Yes | kilograms | > 0 |
| temperature | enum | Yes | `ambient`, `chilled` | Valid enum |
| hazard_class | enum | Yes | `none`, `flammable`, `corrosive` | Valid enum |
| blocked | boolean text | Yes | `true`, `false` | Valid enum; blocked slots cannot be assigned |
| status | enum | Yes | `active`, `inactive` | Valid enum |

## SKU (`skus.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| sku_id | string | Yes | `SKU-000001` pattern | PK |
| warehouse_id | string | Yes | home warehouse ID | FK → Warehouse |
| name | string | Yes | synthetic nonempty name | Not unique |
| category | enum | Yes | `ambient`, `bulk`, `chilled`, `hazmat` | Valid enum |
| status | enum | Yes | `active`, `inactive` | Valid enum |

## SKUProfile (`sku_profiles.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| profile_id | string | Yes | `PRO-000001` pattern | PK |
| sku_id | string | Yes | SKU ID | FK → SKU; unique for the current snapshot |
| warehouse_id | string | Yes | warehouse ID | FK → Warehouse; equals SKU warehouse |
| length_cm | integer | Yes | centimetres | > 0 |
| width_cm | integer | Yes | centimetres | > 0 |
| height_cm | integer | Yes | centimetres | > 0 |
| weight_kg | integer | Yes | kilograms | > 0 |
| temperature | enum | Yes | `ambient`, `chilled` | Valid enum |
| hazard_class | enum | Yes | `none`, `flammable`, `corrosive` | Valid enum |
| effective_from | datetime | Yes | UTC ISO-8601 | ≤ effective_to when present |
| effective_to | datetime | No | UTC ISO-8601 | Empty means current |

Velocity is **derived** by counting `OrderLine` rows per SKU. It is not stored independently in SKUProfile.

## Order (`orders.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| order_id | string | Yes | `ORD-2026-000001` | PK |
| warehouse_id | string | Yes | warehouse ID | FK → Warehouse |
| ordered_at | datetime | Yes | UTC ISO-8601 | ≤ pick time |
| status | enum | Yes | `open`, `fulfilled`, `cancelled` | Valid enum |

## OrderLine (`order_lines.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| order_line_id | string | Yes | `OL-000001-01` | PK |
| order_id | string | Yes | order ID | FK → Order |
| sku_id | string | Yes | SKU ID | FK → SKU; SKU warehouse = Order warehouse |
| quantity | integer | Yes | units | > 0 |
| line_number | integer | Yes | 1-based within order | Unique with order_id |

## PickEvent (`pick_events.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| pick_event_id | string | Yes | `PICK-<order-id>-001` | PK |
| order_line_id | string | Yes | order line ID | FK → OrderLine; one simulated event per line |
| location_id | string | No | location ID | FK → Location when present |
| picked_at | datetime | Yes | UTC ISO-8601 | ≥ Order.ordered_at |
| distance_m | integer | Yes | metres | = route formula for completed event |
| status | enum | Yes | `completed`, `exception` | Valid enum |

## Replenishment (`replenishments.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| replenishment_id | string | Yes | `REP-2026-000001` | PK |
| sku_id | string | Yes | SKU ID | FK → SKU |
| location_id | string | Yes | location ID | FK → Location |
| tasks | integer | Yes | simulated tasks | Derived from ordered units / unit capacity |
| unit_capacity | integer | Yes | units per pick face | floor(Location.capacity_cm3 / SKU unit volume) |
| requested_at | datetime | Yes | UTC ISO-8601 | ≥ order period |
| status | enum | Yes | `simulated` | Valid enum |

## SlotAssignment (`slot_assignments.csv`)

| Field | Type | Required | Allowed values / units | Unique and validation |
| --- | --- | --- | --- | --- |
| slot_assignment_id | string | Yes | `SLOT-2026-000001` | PK |
| sku_id | string | Yes | SKU ID | FK → SKU |
| location_id | string | No | location ID | FK → Location when assigned |
| status | enum | Yes | `active`, `unassigned` in baseline; `proposed` in runs | At most one active per SKU and Location |
| valid_from | datetime | Yes | UTC ISO-8601 | Start of validity |
| valid_to | datetime | No | UTC ISO-8601 | Empty while active; otherwise ≥ valid_from |
| run_id | string | Yes | `BASELINE-2026` or run ID | Traces decision origin |

## Exception fixtures and truth

`exception_fixtures.csv` has `case_id` (unique), `sku_id` (FK → SKU), `location_id` (FK → Location), optional `second_sku_id` (FK → SKU), optional `path_aisle` (positive integer), optional `replenishment_tasks` and `labor_capacity` (nonnegative integer). It contains no label. `ground_truth.csv` has unique `case_id` (FK → fixture), `issue_type` (`capacity_exceeded`, `incompatible_co_location`, `duplicate_active_location`, `blocked_aisle`, `labor_overload`), and `expected_decision` (`reject` or `flag`). It is stored separately from model/optimizer inputs.
