# Slotwise canonical warehouse data contract

**Status:** proposed contract v1.0, 2026-11-01. This document specifies a WMS-neutral CSV import format. It is a design contract, **not an implemented importer or a claim that the current optimizer accepts these files**. The existing `data/starter` and `data/full` files remain legacy synthetic demo inputs. See [the enterprise audit](ENTERPRISE_READINESS_AUDIT.md) for the present implementation and its limitations.

## 1. Design principles and file package

One import is a directory containing `manifest.json` and the CSVs below. An adapter for each WMS maps source IDs, units, statuses and timestamps into this canonical format; the Slotwise engine must consume the canonical package rather than source-specific columns. Keep immutable raw exports separately from the normalized package. Preserve source file/row IDs in the adapter's lineage report; never silently generate physical attributes or a warehouse ID from a sales channel.

| File | Purpose | Import mode |
| --- | --- | --- |
| `warehouses.csv`, `zones.csv`, `locations.csv` | Physical master data and current availability | Complete snapshot |
| `skus.csv`, `sku_profiles.csv`, `sku_packaging_profiles.csv`, `sku_lots.csv` | Product identity, effective storage rules, measured stock-pack geometry and tracked lot/serial identities | Complete current snapshot plus referenced history needed for calibration |
| `sku_uom_conversions.csv` | Approved SKU-specific source-to-base quantity conversions | Required for non-base source UOMs unless each source row carries an actual measured base quantity for a variable conversion |
| `inventory_by_location.csv` | Actual quantity occupying each bin | Complete, point-in-time snapshot |
| `slot_assignments.csv` | Current pick/reserve designation; may have several locations per SKU | Complete, point-in-time snapshot |
| `orders.csv`, `order_lines.csv` | Historical demand and fulfillment context | Optional bounded history extract; both files together when supplied |
| `pick_events.csv`, `replenishment_events.csv` | Actual timestamped work, including partial tasks | Optional bounded event history; either file may be supplied independently |
| `historical_location_refs.csv` | Identity/warehouse link for retired bins referenced only by historical tasks | Optional; required only when such event IDs cannot resolve to current `locations` |
| `aisle_nodes.csv`, `aisle_edges.csv`, `location_access.csv` | Walkable warehouse graph and bin access points | Complete layout snapshot |
| `location_pack_capacities.csv` | Site-certified quantity capacity for one pack type in an otherwise empty bin | Required for quantity-bearing recommendations unless a separately validated packing solver is approved |
| `co_storage_rules.csv` | Explicit shared-bin product compatibility | Required to approve an occupied or proposed mixed-SKU bin; absence marks existing mixtures as incidents and blocks new mixtures |
| `shared_bin_plans.csv`, `shared_bin_plan_items.csv` | Approved jointly packable composition with more than one physical package profile, including mixed-SKU bins | Required for a mixed-package recommendation unless a separately validated joint packing solver is approved |
| `location_hazard_permissions.csv`, `hazard_segregation_rules.csv`, `hazard_separation_evidence.csv` | Approved hazard placement and physical separation policy | Permissions and group-pair rules required for all represented groups, including `none`; pair evidence conditional on clearance/barrier rules |

`import_kind=full_snapshot` requires `warehouses`, `zones`, `locations`, `skus`, `sku_profiles`, `sku_packaging_profiles`, `inventory_by_location`, `slot_assignments`, `aisle_nodes`, `aisle_edges`, `location_access`, `location_hazard_permissions` and `hazard_segregation_rules` CSVs, even when some have zero rows. `sku_lots` is required when tracked current stock or a supplied event references a lot. Other policy/capacity files are required only for the corresponding candidates, as stated below. Historical files are independent: a stock-and-layout snapshot with no orders/events supports capacity diagnostics but no demand ranking. `import_kind=demand_only` requires the core `warehouses`, `skus`, `orders` and `order_lines`, plus `sku_uom_conversions` if needed; it cannot produce operational slot recommendations and does not accept pick/replenishment events without the physical reference masters. Optional history/event files in the table above apply to `full_snapshot`. Every supplied file is listed in the manifest; absence is a capability limit, not fabricated zero activity.

**Encoding and values.** CSV must be UTF-8 with a header row, comma delimiter and RFC-style quoting. Header names are exactly the snake_case names below. Required values must be present; optional unknown values are empty fields, never `0`, `unknown`, or fabricated data. IDs are opaque stable strings (case-sensitive, no surrounding whitespace); uniqueness is within a tenant/import history. The adapter maintains stable mappings across imports and namespaces conflicting WMS IDs. All quantities are base-10 decimals, parsed with decimal arithmetic; no thousands separator or `NaN`. Dimensions are **cm**, weights **kg**, usable volume **cm³**, route lengths **m**, durations **s**, temperatures **°C**. All stock and work quantities use the SKU's canonical `base_uom` and `quantity_precision`; `handling_unit_count` and certified pack counts are whole physical packages. All timestamps are ISO-8601 UTC with `Z` or `+00:00`; source time zones and daylight-saving conversion are recorded by the adapter. Enumerations are lower-case tokens. Booleans are `true`/`false`. No customer names or addresses are required.

**Legend for schema tables.** `R` = required on every row, `O` = optional, `C` = conditionally required as stated. `—` means dimensionless/not applicable. `id` and `text` are UTF-8 strings; `decimal` is exact base-10; `integer` is a nonnegative whole number unless a stricter rule is stated; `date` is ISO-8601 `YYYY-MM-DD` in the warehouse's local calendar; `timestamp` is UTC ISO-8601. Each file's first ID column is its primary key unless a composite key is specified. Repeated `warehouse_id` fields are deliberate: they allow independent cross-warehouse checks. Source-specific codes must be mapped to documented canonical enumerations or rejected.

### `manifest.json` (one per import)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `schema_version` | text | R | — | Exactly `1.0` for this contract; reject unsupported major versions. |
| `dataset_id` | id | R | — | Unique immutable import identifier. |
| `tenant_id` | id | R | — | Tenant/account scope; every ID and file belongs to this tenant. |
| `source_system` | text | R | — | Primary WMS or named integration bundle and adapter identity; nonempty. Contributing systems are identified per source extract. |
| `mapping_version` | text | R | — | Version of approved source-to-canonical mapping. |
| `storage_policy_version` | text | C | — | Required for `full_snapshot`; nonempty immutable **bundle** version identifying site-approved temperature, co-storage, hazard and packing policies for each warehouse at `snapshot_at`. Lineage lists each site's actual approval/version. Blank for `demand_only`, which has no physical policy claim. |
| `exported_at` | timestamp | R | UTC | Source export completion time; `snapshot_at ≤ exported_at ≤ import processing time`. |
| `snapshot_at` | timestamp | R | UTC | For `full_snapshot`, one **exact** logical as-of time for physical masters, inventory, assignments, packaging and layout; never a tolerance window. For `demand_only`, as-of time for order/line statuses and SKU identity, with no physical-state claim. |
| `consistency_method` | enum | R | — | `transactional`, `replayed` or `verified_quiescent` for `full_snapshot`; `history_extract` only for `demand_only`. Identifies how the supplied tables were made consistent at `snapshot_at`. |
| `consistency_token` | text | C | — | Required only for `transactional`: a source snapshot/transaction token covering every snapshot entity, or documented equivalent tokens proven to share one source transaction. Never an invented import ID. |
| `replay_proof_id` | text | C | — | Required only for `replayed`: points to a complete, checked change-log replay of **every** snapshot entity through `snapshot_at`; absent/partial replay cannot qualify. |
| `quiescence_ref` | text | C | — | Required only for `verified_quiescent`: signed site/source attestation that inventory, assignments and relevant masters/layout were unchanged throughout all extract capture times, with source control totals and pause/resume times. `snapshot_at` must fall in that interval. |
| `history_extract_ref` | text | C | — | Required only for `history_extract`: approved extract job/cut ID covering `orders`, `order_lines` and SKU identity at their declared as-of time; order/line counts and FKs must reconcile. This does **not** certify physical snapshot consistency. |
| `history_from`, `history_to` | timestamp | C | UTC | Both required if orders/events are provided; `from < to ≤ snapshot_at`, with `[from,to)` semantics for timestamped task events. Referenced orders may predate `history_from`. Date-only orders use their warehouse-local calendar dates; their exact UTC inclusion within a boundary day is unknown and must be reported as date-grain coverage. |
| `warehouse_ids` | array of id | R | — | Nonempty; exactly the warehouse IDs represented by the snapshot. |
| `files` | object | R | — | One entry per supplied CSV with `filename` (unique relative basename), `rows` (integer ≥0), `sha256` (64 lowercase hex), and `source_extracts` array (nonempty if rows >0). Each extract has `source_system` (nonempty contributing WMS/ERP ID), `source_extract_id` (stable nonempty lineage ID) and `captured_at` (UTC timestamp ≤ `exported_at`). A zero-row file with no extract needs `empty_scope_ref` proving it was intentionally empty for the declared warehouse scope, not omitted by accident. Source-row lineage identifies its extract; one canonical CSV may combine extracts. Exact file set, row counts and bytes must match. |
| `source_timezones` | object | R | IANA names | Nonempty mapping from every contributing `source_system` to its valid source time zone before UTC conversion, e.g. `Asia/Kolkata`; no system used by `files.source_extracts` may be missing. Per-row source offsets/time-zone exceptions remain in lineage. |
| `import_kind` | enum | R | — | `full_snapshot` or `demand_only` with file requirements above; both are complete for their declared scope, not incremental deltas. |

The manifest is a JSON envelope while warehouse records remain CSV. For `full_snapshot`, a multi-file export captured at different times is **not** made consistent by giving every row the same timestamp. Prove a shared source snapshot, complete replay or a verified no-change capture interval across all contributing systems; an ERP and WMS snapshot with different clocks/tokens needs a documented join cut and reconciliation. Quiescence requires an actual operational pause or source change lock, not an assumption of low activity. If physical consistency cannot be established, accept files for diagnostics only. `history_extract` proves only the bounded sales extract and its parent/line joins. Active profiles and policies must cover `snapshot_at`; referenced historical events require versions effective at their event times **only for the historical rule-dependent analysis being claimed**. Files may contain multiple warehouses, but each relationship must remain within its declared warehouse. Warehouses with different as-of times or consistency methods use separate import packages; a one-warehouse first pilot is preferred.

## 2. Canonical entity schemas

### `warehouses.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `warehouse_id` | id | R | — | PK; listed in manifest; stable physical facility ID. |
| `name` | text | R | — | Nonempty display name. |
| `timezone` | text | R | IANA name | Valid warehouse local time zone; UTC stored in all event files. |
| `status` | enum | R | — | `active`/`inactive`; only active facilities eligible for recommendations. |
| `default_pick_node_id` | id | C | — | Required for route estimates in an active warehouse; FK → `aisle_nodes.node_id`, same warehouse, active node type `depot` or `packing`, reachable by picker graph. Blank is allowed in a `demand_only` package or for an inactive warehouse; never invent a depot. The warehouse/node FKs are checked after both CSVs load. |
| `operating_notes` | text | O | — | Display-only; never used as a rule. |

### `zones.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `zone_id` | id | R | — | PK. |
| `warehouse_id` | id | R | — | FK → `warehouses`; immutable parent within an import. |
| `name` | text | R | — | Nonempty. |
| `zone_type` | enum | R | — | `ambient`, `chilled`, `frozen`, `hazmat`, `bulk`, `other`; physical meaning documented by adapter. |
| `status` | enum | R | — | `active`/`inactive`; inactive zones block all child bins. |
| `equipment_code` | text | O | — | Site-approved equipment restriction code; unknown code must not be ignored. |

### `locations.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `location_id` | id | R | — | PK; stable bin/barcode ID. |
| `warehouse_id` | id | R | — | FK → `warehouses`; must equal parent zone warehouse. |
| `zone_id` | id | R | — | FK → `zones`; same warehouse. |
| `location_type` | enum | R | — | `pick`, `reserve`, `both`, `staging`; only `pick`/`both` can be a pick face. |
| `status` | enum | R | — | `active`/`inactive`; inactive blocks new placement. |
| `blocked` | boolean | R | — | `true` blocks placement and routing access at `snapshot_at`; source and reason retained in lineage. |
| `usable_length_cm`, `usable_width_cm`, `usable_height_cm` | decimal | C | cm | All three required for a candidate storage bin; may be blank for an unmeasured current/staging/inactive bin. Each supplied value >0 and measured clear, not nominal, dimensions. Missing geometry makes that bin ineligible. |
| `usable_volume_cm3` | decimal | C | cm³ | Required for a candidate; >0 and ≤ product of its clear dimensions. It is an upper bound on occupied outer-package volume, **not** proof of geometric packability. Blank means no capacity assertion. |
| `max_total_weight_kg` | decimal | C | kg | Required for a candidate; >0. Aggregate weight of physical residual stock plus proposed packages must fit. Blank means load safety is unverified; an empty assignment has no physical weight. |
| `max_distinct_skus` | integer | C | SKUs | Required for a candidate; ≥0. `0` forbids new placement; values >1 permit sharing subject to policy. Blank for an unmeasured bin means sharing policy is unknown and blocks that bin. Existing stock above a known limit is an incident, not silently removed. |
| `allow_mixed_lots` | boolean | C | — | Required for a candidate containing/tracked for lots; false means at most one lot/serial of a SKU in the bin. Blank means unknown lot policy and blocks tracked-stock placement. |
| `max_lots_per_sku` | integer | C | lots/SKU | Required alongside `allow_mixed_lots` for a tracked-stock candidate; ≥1 and must be 1 when mixing is false. Counts physically present distinct lots of each SKU. |
| `temperature_class` | enum | R | — | `ambient`, `chilled`, `frozen`, `controlled`; documented facility designation. Temperature safety uses certified numeric intervals; a `controlled` SKU additionally requires a `controlled` location. Other class differences do not by themselves reject a numerically safe placement. |
| `operating_temp_min_c`, `operating_temp_max_c` | decimal | C | °C | Both required for a candidate, otherwise both or neither; `min ≤ max`. They are certified normal operating bounds, not one sensor reading. Missing defensible bounds make the bin ineligible. |
| `pick_height_cm` | decimal | O | cm | ≥0; measured ergonomic/reach height, not a route-coordinate substitute. |
| `aisle_label`, `bay_label`, `level_label` | text | O | — | Human-readable labels; **not** used to derive walk distance. |
| `equipment_code` | text | O | — | Required by site policy for constrained access; must resolve in approved policy dictionary. |

Locations do not contain a single `sku_id`. A complete snapshot retains unmeasured, inactive, blocked and staging bins with real stock; missing candidate-only measurements create **unverified-current-state incidents**, not fabricated values or automatic deletion of stock. Capacity is evaluated against the **post-move** stock state: stock remaining after documented removals plus stock added, across every SKU, package and lot. Never add a proposed quantity on top of stock that the same approved move removes, and never subtract stock before that move is feasible. Gross package weight and outer volume are summed across all records. Passing aggregate weight/volume limits is necessary but **not sufficient**: individual orientation/height, stacking and a certified pack limit or verified packing plan must also pass. Each proposed move must specify the actual destination packaging profile, package count and opened/sealed state; base quantity alone does not determine footprint. A mixed-SKU bin additionally requires explicit co-storage permission and a verified mixed-pack arrangement; until that check exists, the bin may be represented as current state but cannot receive a new shared-bin recommendation. Separate measured compartments may instead be imported as distinct locations with their own stable IDs and capacities; parent and child capacities must not be counted twice. Where several bins share a shelf/beam/rack load limit, the site must certify non-overlapping allocated bin limits that remain safe at simultaneous maximum loads; otherwise those bins are ineligible for new placement under v1.

### `historical_location_refs.csv` (historical identity only)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `location_id` | id | R | — | PK; retired historical bin ID referenced by a task, disjoint from current `locations.location_id`. Never a candidate placement. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals the referencing task warehouse. |
| `retired_at` | timestamp | O | UTC | If known, ≤ `snapshot_at`; does not assert the exact bin geometry or layout at an older event time. |

Historical task bin IDs resolve to exactly one of current `locations` or `historical_location_refs`. A former bin can remain as an inactive row in current `locations` instead. A historical-only reference validates identity and facility, **not** historical geometry, route distance, capacity or safety; those require separate time-appropriate evidence. Current inventory and slot assignments may reference only current `locations`.

### `skus.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `sku_id` | id | R | — | PK; tenant-wide stable SKU ID; a SKU may be stocked in many warehouses. |
| `name` | text | R | — | Nonempty display name. |
| `category_code` | text | O | — | Analytics grouping only; never infers safety requirements. |
| `status` | enum | R | — | `active`, `inactive`, `discontinued`; historical events may reference inactive SKUs, new placement requires explicit policy. |
| `is_stock_item` | boolean | R | — | False excludes physical slotting unless a site-specific exception is approved. |
| `base_uom` | enum | R | — | `ea`, `case`, `pallet`, `kg`, `l`, or approved extension; **all quantities for a SKU** are converted to this unit. Slotting requires each actual stored package to have a fixed measured profile and count, including for `kg`/`l`; unpackaged variable-weight/bulk stock is outside v1 slotting. |
| `quantity_precision` | integer | R | decimal places | 0–6; quantities must be exact multiples of `10^-quantity_precision`. |
| `lot_tracking` | enum | R | — | `none`, `lot`, `expiry`, `serial`; determines required inventory identifiers and expiry checks. |

`base_uom` is the canonical **quantity** unit. `sku_profiles.csv` carries storage/safety rules and optional bare-base-unit geometry; `sku_packaging_profiles.csv` describes each actual stored package. A case-only operation can be eligible without dimensions for one loose each, provided its cases have measured package profiles and counts. An order in cases and inventory in eaches must use approved, effective-dated SKU-specific conversions; missing/ambiguous conversion or precision loss blocks the affected quantity. A source unit label alone never proves a package size. A SKU has no single mandatory home warehouse.

### `sku_profiles.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `profile_id` | id | R | — | PK; immutable profile version. |
| `sku_id` | id | R | — | FK → `skus`. |
| `warehouse_id` | id | O | — | FK → `warehouses` when site-specific override; blank is tenant-wide default. At most one effective profile **per scope**; a site override takes precedence over a global profile. |
| `effective_from` | timestamp | C | UTC | Required when the profile is effective-dated or used for historical replay; may be blank only for a current snapshot-only profile valid at `snapshot_at`. |
| `effective_to` | timestamp | O | UTC | If present, requires `effective_from` and must be later; half-open `[from,to)`. Blank end means open-ended for an effective-dated profile. |
| `unit_length_cm`, `unit_width_cm`, `unit_height_cm` | decimal | O | cm | All three or none; each >0 when present, measured outside dimensions of one bare base unit. Required only if that bare physical form is stored/proposed and no independently measured `base` package profile supplies its geometry. |
| `unit_weight_kg` | decimal | O | kg/base unit | >0 when present; measured gross bare-unit handling weight. Required only if bare base units are stored/proposed without an independently measured `base` package weight. |
| `orientation_policy` | enum | O | — | `fixed`, `rotate_horizontal`, `rotate_any` when present; required for bare-unit fit if no independently approved `base` package orientation. |
| `stackable` | boolean | O | — | Required for bare-unit stacking if no independently approved `base` package rule; false prohibits stacking that bare form, not an independently certified case/pallet. |
| `max_stack_units` | integer | C | base units | ≥1 when `stackable=true`; blank when false or `stackable` is blank. Additional load/bearing rules may still limit stacking. |
| `temperature_class` | enum | R | — | `ambient`, `chilled`, `frozen`, `controlled`; `controlled` means a certified controlled location is required. For other classes, the numeric bounds below decide temperature fit even when labels differ. Additional humidity/light controls require explicit site policy; absent controls block affected SKUs. |
| `storage_temp_min_c`, `storage_temp_max_c` | decimal | R | °C | Both required for every class, `min ≤ max`; approved SKU storage bounds. The location's entire certified operating interval must fall within this interval (`SKU min ≤ location min ≤ location max ≤ SKU max`). Unknown ambient tolerance blocks new placement. |
| `hazard_group` | text | R | — | `none` or approved site safety group; must have explicit location permission. Unknown is not `none`. |
| `co_storage_group` | text | R | — | Approved site group for allergen, odor, contamination and other shared-bin restrictions; cannot be inferred from product category. |

Bare-unit dimensions, when known, are used for bare-unit eligibility and a consistency check against an effective `base` packaging profile. Actual occupancy always uses the physical package form recorded on each inventory row. A missing optional bare-unit measurement cannot be invented from a case size. Volume division alone cannot establish how many packages fit; stacking has to respect both layer count and supported top load. Profile precedence is site-specific effective profile, otherwise global effective profile. Overlap **within** either scope is rejected; intentional global/warehouse overlap is resolved by that precedence.

### `sku_uom_conversions.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `conversion_id` | id | R | — | PK; recorded in per-row import lineage whenever a source quantity is converted. |
| `sku_id` | id | R | — | FK → `skus`; conversions are SKU-specific, never a global rule for all `case` values. |
| `warehouse_id` | id | O | — | FK → `warehouses` for site-specific pack definitions; blank is global. Same precedence as SKU profiles. |
| `source_system` | text | R | — | Nonempty contributing system identifier, present in the relevant source row's `files.source_extracts`; prevents identical source codes in different systems from sharing an unapproved conversion. |
| `source_uom` | text | R | — | Nonempty approved source code, scoped by `source_system`; exact case-sensitive mapping. |
| `base_units_per_source_unit` | decimal | R | SKU base units/source unit | >0, exact finite decimal factor; converted quantity must fit SKU `quantity_precision` exactly. If source UOM equals `base_uom`, factor must be 1. A nonterminating ratio needs a separately approved exact measured quantity, not a rounded factor. |
| `effective_from` | timestamp | R | UTC | Start inclusive; must cover every converted source transaction's business timestamp. |
| `effective_to` | timestamp | O | UTC | End exclusive; if present, > `effective_from`; no overlapping rule for `(SKU, scope, source_system, source_uom)` and time. |

The adapter records raw quantity/UOM, selected `conversion_id` (or explicit identity conversion), exact converted quantity and source extract/row ID in lineage. If source UOM already equals the SKU base UOM, factor 1 is valid without a conversion table row. For a date-only order, an effective rule is usable only if the same factor covers that **entire warehouse-local day**; a mid-day change makes its quantity ambiguous. The adapter rejects rounding beyond `quantity_precision` and cannot silently change a physical pack count. For catch-weight or other variable conversions, each source transaction needs an **actual measured base quantity** and its source value; a fixed scalar rule is forbidden. Without measured quantity and a fixed physical container profile, such stock is not eligible for v1 slot recommendations.

### `sku_packaging_profiles.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `packaging_profile_id` | id | R | — | PK; immutable version of a physical stock/handling package. |
| `sku_id` | id | R | — | FK → `skus`; must resolve to the same SKU as inventory/pick/replenishment row using it. |
| `warehouse_id` | id | O | — | FK → `warehouses` for site override; blank is global, with one effective profile per packaging code and scope. |
| `packaging_code` | text | R | — | Stable `base`, `case`, `pallet`, or site code; `base` profile required only when that physical form occurs in current/proposed stock. Case/pallet-only SKUs need only their actual package profiles. |
| `base_units_per_pack` | decimal | R | SKU base units/package | >0; `base` must equal 1 and agree with effective `sku_profiles` physical unit. |
| `outer_length_cm`, `outer_width_cm`, `outer_height_cm` | decimal | R | cm | Each >0; measured outside package dimensions. |
| `gross_weight_kg` | decimal | R | kg/package | >0; measured full-package weight including packaging and contents. If bare-unit weight is known, compare with `base_units_per_pack × unit_weight_kg`; a lower value requires documented unpacking/packaging-hierarchy evidence and approval, never silent acceptance. |
| `orientation_policy` | enum | R | — | `fixed`, `rotate_horizontal`, `rotate_any`; approved for this package, may be stricter than base unit. |
| `stackable` | boolean | R | — | False prohibits stacking this package. |
| `max_stack_layers` | integer | C | packages high | ≥1 if stackable; blank if not stackable. |
| `max_supported_top_load_kg` | decimal | C | kg | ≥0 if stackable; certified load borne by one package, including all layers above it. |
| `effective_from` | timestamp | R | UTC | Start inclusive; must cover current inventory at `snapshot_at`. Historical versions are required only for package-dependent historical replay, not aggregate pick/replenishment event counts. |
| `effective_to` | timestamp | O | UTC | End exclusive; > start if present; no overlapping version per `(SKU, scope, packaging_code)`. |

An effective `base` package has `base_units_per_pack=1`; when the bare-unit SKU profile carries measurements, its dimensions/weight must be consistent or the difference needs a reviewed mapping. The independently measured `base` package may supply geometry when bare-unit measurements are absent. An order UOM conversion and a stock packaging profile are **different concepts**: a source `case` quantity may convert to 12 eaches, while inventory may be stored as sealed cases, opened cases or loose eaches.

### `sku_lots.csv` (required for tracked SKUs)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `lot_record_id` | id | R | — | PK; one row for each distinct `(sku_id, lot_id)` referenced by current inventory or imported events. |
| `sku_id` | id | R | — | FK → `skus`; its `lot_tracking` must be `lot`, `expiry` or `serial`. |
| `lot_id` | text | R | — | Nonempty source lot/serial ID; unique together with `sku_id` across tenant. |
| `expiry_at` | timestamp | C | UTC | For a `lot_tracking=expiry` lot in current inventory, exactly one of `expiry_at` or `expiry_on` is required. Use this field for an exact source expiry instant. Optional for historical-only lots absent from stock. |
| `expiry_on` | date | C | warehouse local date | Alternative to `expiry_at` for a source **last eligible calendar day**; exactly one expiry field for current expiry-tracked stock. At each warehouse, stock is expired after that local day ends. The adapter must document source semantics; ambiguous date meaning blocks expiry-based recommendations. |
| `received_at` | timestamp | O | UTC | Source receipt time; if present, ≤ `snapshot_at` for current stock. |
| `disposition_at_snapshot` | enum | C | — | `released`, `quarantine`, `recalled`, `expired`; required for lots in current stock, optional for historical-only lots no longer present. Current stock may be `available` only when released and not expired. Historical picks use disposition effective **at pick time**, if available, not this current flag. |

This file may be built from authoritative lot fields in several WMS exports, but the adapter must retain every source reference and reject conflicting expiry/disposition data. A date-only expiry is evaluated in each `warehouses.timezone`; a lot shared across warehouses can therefore have different expiry instants, while its source expiry date remains one value. A current snapshot does not prove where a lot was stored in the past; location-level historical replay requires historical inventory/transaction evidence. Serial IDs must not appear at two locations in the same snapshot.

### `inventory_by_location.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `inventory_record_id` | id | R | — | PK; stable within the export. |
| `warehouse_id` | id | R | — | FK → `warehouses`; must equal location warehouse. |
| `location_id` | id | R | — | FK → `locations`. |
| `sku_id` | id | R | — | FK → `skus`; profile must resolve for this warehouse. |
| `lot_id` | text | C | — | Required and FK → `sku_lots.(sku_id, lot_id)` when SKU `lot_tracking` is `lot`, `expiry` or `serial`; blank only for `none`. A serial ID is a lot key for one base unit. |
| `packaging_profile_id` | id | R | — | FK → effective `sku_packaging_profiles`, same SKU and warehouse scope. Identifies the actual physical package, not order UOM. |
| `handling_unit_count` | integer | R | physical packages | ≥1 actual packages present; do not infer opened-case count from total eaches. |
| `pack_state` | enum | R | — | `sealed`, `opened`, `loose`; sealed quantity equals package count × base units/pack. For opened packs, each counted package contains at least one minimum representable base quantity: `handling_unit_count × 10^-quantity_precision ≤ on_hand_qty ≤ handling_unit_count × base_units_per_pack`. Loose uses the `base` package and quantity equals package count. Empty cartons are not inventory; if they obstruct a bin, record them in site capacity evidence. |
| `stock_status` | enum | R | — | `available`, `quarantine`, `damaged`, `expired`, `other_nonpickable`; all physically occupy capacity, only `available` may be picked. `other_nonpickable` retains its exact source state in lineage and cannot be treated as available by default. |
| `on_hand_qty` | decimal | R | SKU base units | >0; physically present, including reserved/held units. No row for an empty bin. |
| `reserved_qty` | decimal | R | SKU base units | ≥0 and ≤ `on_hand_qty`. |
| `hold_qty` | decimal | R | SKU base units | ≥0; `reserved_qty + hold_qty ≤ on_hand_qty` after adapter maps overlapping source flags to disjoint quantities. Non-available stock may retain source reservations/holds for reconciliation but has zero pickable quantity. If overlap cannot be resolved, retain the physical stock for diagnostics and block availability-dependent recommendations. |
| `snapshot_at` | timestamp | R | UTC | Must equal manifest `snapshot_at` exactly after transactional extraction, verified replay or attested quiescent capture; no per-row tolerance. |

Logical key `(warehouse_id, location_id, sku_id, lot_id, packaging_profile_id, pack_state, stock_status)` is unique at a snapshot; multiple source rows with this same key must be reconciled and aggregated with retained lineage, not rejected merely for source-level splitting. Multiple rows per SKU across locations and multiple SKUs/lots/packages in a shared location are allowed. For `lot_tracking=serial`, `(sku_id, lot_id)` is unique across the entire physical snapshot, `on_hand_qty=1`, and the base package is used. `available_qty = on_hand_qty - reserved_qty - hold_qty` applies only to `stock_status=available` **and**, for tracked SKUs, a released, unexpired lot; otherwise it is 0. An untracked SKU with available stock is not required to have a lot row. FEFO/lot selection must operate on these quantities and the applicable `sku_lots.expiry_at` or warehouse-local `expiry_on`, not on SKU totals alone. All physical packages, including quarantine/expired stock and opened packs, occupy capacity. A package's gross weight and outer bounding volume are conservatively counted **once** per physical package; do not represent both a loaded pallet and its constituent cases as separate inventory rows. Source stock states that overlap or cannot distinguish packages require a reviewed adapter mapping or a physical audit, not double-counting or guessed pack counts.

### `location_pack_capacities.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `capacity_id` | id | R | — | PK; unique active `(location_id, packaging_profile_id)` certificate at `snapshot_at`. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals location warehouse. |
| `location_id` | id | R | — | FK → `locations`; active, measurable bin. |
| `packaging_profile_id` | id | R | — | FK → effective `sku_packaging_profiles` with global or this warehouse scope; one package type in an otherwise empty bin. A profile overridden for another warehouse is invalid here. |
| `max_handling_unit_count` | integer | R | physical packages | ≥1 certified upper bound; must also satisfy bin total weight, usable volume, single-package orientation and stack limits. |
| `certified_at` | timestamp | R | UTC | ≤ manifest `snapshot_at`; tied to measured bin/package versions. |
| `valid_until` | timestamp | O | UTC | If present, > manifest `snapshot_at`; stale certificates are invalid. |
| `basis` | enum | R | — | `site_test`, `vendor_spec`, `engineering`; source evidence and responsible approver retained in lineage. |
| `approval_ref` | text | R | — | Nonempty site-approved evidence/reference ID. |

This certificate bounds one package type in an **empty** location; it is not leftover capacity or a mixed-package packing certificate. Candidate quantity must be converted to actual package count, considering sealed/opened state, and remain within this bound **and** all load/volume/stack rules. If a WMS cannot supply a certified capacity, a separately validated geometric packing method may replace this table for that pair; the run must name and version that method. Until one of these exists, a recommendation may say that one package individually fits but must **not** assert that a replenishment quantity fits. For an occupied bin containing more than one package profile, even for one SKU, a verified joint packing plan or measured separate compartments is additionally required; independent certificates cannot be added together.

### `slot_assignments.csv` (actual current layout, not a proposal)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `slot_assignment_id` | id | R | — | PK; stable current/historical assignment ID. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals location warehouse. |
| `sku_id` | id | R | — | FK → `skus`; **not unique** in this file. |
| `location_id` | id | R | — | FK → `locations`; **not unique** if location permits sharing. |
| `role` | enum | R | — | `pick`, `reserve`, `both`; pick role only in pickable bin. |
| `status` | enum | R | — | `active`/`inactive` for imported current state. Proposed assignments live in versioned output runs, not input. |
| `snapshot_at` | timestamp | R | UTC | Must equal manifest `snapshot_at` exactly; canonical as-of state established by source transaction, replay or attested quiescent capture. |
| `valid_from`, `valid_to` | timestamp | O | UTC | Effective-dated history when source provides it; `from < to`, half-open `[from,to)`. Blank `valid_to` means current. |
| `last_changed_at` | timestamp | O | UTC | Source change time if known; ≤ `snapshot_at`. It does not replace the common snapshot time. |
| `min_pick_qty`, `max_pick_qty` | decimal | O | SKU base units | Independently optional, each ≥0; if both supplied, `min ≤ max`. A known bound is usable alone; a missing bound is not inferred from inventory. |
| `pick_priority` | integer | O | rank | ≥1; used only if site confirms priority semantics. |

At `snapshot_at`, one SKU may have several active pick faces and reserve bins. At most one active assignment exists per `(warehouse_id, sku_id, location_id)`; role `both` replaces, rather than duplicates, pick/reserve roles. If effective dates are present, an active row must cover `snapshot_at`, and inactive history must not overlap a conflicting active row. With both dates blank the row states current snapshot only and cannot be used to reconstruct historical assignments; `valid_to` alone is invalid. A bin may contain several active SKUs only when `max_distinct_skus` permits it and explicit co-storage, hazard, lot and physical constraints pass. An inventory row without an assignment is a legitimate exception or reserve/staging state to classify, not automatically a missing SKU. An assignment without stock may be valid for an empty reserved pick face but must be flagged for review. Active assigned SKUs **and** residual physical stock count when evaluating a proposed bin configuration.

### `co_storage_rules.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `co_storage_rule_id` | id | R | — | PK; approved site policy for any mixed-SKU bin being validated. |
| `warehouse_id` | id | R | — | FK → `warehouses`. |
| `group_a`, `group_b` | text | R | — | Unordered pair of approved site `co_storage_group` codes; current SKU-profile groups must resolve to those codes. Self-pairs included when distinct SKUs share a group; approved future group codes may appear without a current SKU. |
| `allowed` | boolean | R | — | Explicit same-bin permission; missing pair means **not allowed**, including unknown groups. |
| `approval_ref` | text | R | — | Nonempty site policy approval/evidence reference. |
| `effective_from` | timestamp | R | UTC | Start inclusive; active rule must cover manifest `snapshot_at`. |
| `effective_to` | timestamp | O | UTC | End exclusive; > start if present; no overlapping rule for a pair in a warehouse. |

Normalize `(group_a,group_b)` as an unordered key (for example by bytewise sorting); reversed duplicate active rows are conflicts. For every pair of **different SKUs** physically present or proposed in one location, require `max_distinct_skus` headroom and an active `allowed=true` pair rule. Check each SKU's temperature range, hazard permission, hazard pair rule and lot policy independently; a co-storage rule cannot override them. Pair permission also does not prove joint geometric packing. Existing mixed bins lacking an approved pair or pack plan are reported as incidents and excluded from new shared-bin proposals, not silently treated as safe.

### `shared_bin_plans.csv` and `shared_bin_plan_items.csv`

The `plan:` and `item:` labels below identify the CSV; they are **not** part of the header names. Each file has its own listed fields and primary key.

| File / field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| plan: `plan_id` | id | R | — | PK; approved jointly packable configuration, not a generic independent capacity. |
| plan: `warehouse_id` | id | R | — | FK → `warehouses`; equals bin warehouse. |
| plan: `location_id` | id | R | — | FK → `locations`; at most one active plan selected for a proposed bin composition. |
| plan: `certified_at` | timestamp | R | UTC | ≤ `snapshot_at`; based on current bin dimensions, pack versions and storage policy. |
| plan: `valid_until` | timestamp | O | UTC | If present, > `snapshot_at`; invalid after geometry, rack or package changes. |
| plan: `approval_ref` | text | R | — | Nonempty site engineering/safety evidence reference; includes orientation, stack pattern and top-load verification at simultaneous maxima. |
| item: `plan_item_id` | id | R | — | PK; exactly one item per `(plan_id, packaging_profile_id)`. |
| item: `plan_id` | id | R | — | FK → `shared_bin_plans`. |
| item: `packaging_profile_id` | id | R | — | FK → effective `sku_packaging_profiles` with global or parent plan warehouse scope; its SKU is the item's SKU. A different warehouse's override cannot be used. |
| item: `max_handling_unit_count` | integer | R | physical packages | ≥1; **all** item maxima must have been verified to fit **simultaneously**. |

For any bin with multiple physical package profiles, every type in residual and proposed stock must appear in one current plan, and the count for each type must not exceed its item maximum. The plan is a conservative capacity envelope: every lower-count combination must remain safe under the approved packing pattern. Recheck total weight, summed outer volume, orientation, stacking/top load, lot limits, co-storage and hazards after the proposed move. A plan cannot authorize a package SKU absent from its items. If the bin is not covered by such a plan, the only alternative is an independently validated, named and versioned joint packing method; neither individual fit nor the sum of single-profile capacity certificates suffices.

### `orders.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `order_id` | id | R | — | PK; stable within tenant and source history. |
| `warehouse_id` | id | R | — | FK → `warehouses`; actual fulfillment facility, not sales channel or customer city. |
| `ordered_at` | timestamp | C | UTC | Required unless `ordered_on` is supplied; real order creation/acceptance time, ≤ `snapshot_at`. May predate `history_from` when an older open order is referenced by an in-window event. Never invent a time of day. |
| `ordered_on` | date | C | warehouse local date | Required unless `ordered_at` is supplied; when both are present, must equal the local calendar date of `ordered_at` in `warehouses.timezone`. Date-only orders support daily demand, not within-day sequencing or time-based service metrics. |
| `status` | enum | R | — | `open`, `allocated`, `partially_fulfilled`, `fulfilled`, `cancelled`, `other`; adapter publishes exact source mapping. `other` retains a real source state in lineage and is excluded from demand/fulfillment metrics until its business meaning is approved. |
| `due_at` | timestamp | O | UTC | ≥ `ordered_at` when exact order time exists; otherwise its warehouse-local date must be ≥ `ordered_on`. Service-time analysis needs exact order time. |
| `channel_code` | text | O | — | Sales channel; analytics feature, never a warehouse ID. |
| `wave_id` | id | O | — | Source wave/batch if all lines share it; line/event-level wave may override. |

### `order_lines.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `order_line_id` | id | R | — | PK. |
| `order_id` | id | R | — | FK → `orders`. |
| `sku_id` | id | R | — | FK → `skus`; SKU need not have a single home warehouse. |
| `line_number` | integer | O | sequence | ≥1, unique within order when present. |
| `ordered_qty` | decimal | R | SKU base units | >0; converted from source UOM with recorded conversion. |
| `fulfilled_qty` | decimal | O | SKU base units | ≥0 when supplied. If it exceeds `ordered_qty`, retain the real value as an overfulfillment/correction exception, require source lineage and exclude it from ordinary demand/savings calibration until reconciled; do not silently cap or reject the order. Returns are separate signed business events outside this v1 line quantity. |
| `status` | enum | R | — | `open`, `allocated`, `partially_fulfilled`, `fulfilled`, `cancelled`, `other`; cannot contradict parent lifecycle without a documented partial state. `other` retains its source value and is excluded from ordinary demand/fulfillment metrics pending mapping. |
| `wave_id` | id | O | — | Batch/wave for this line when known. |

Ordered frequency, fulfilled units and **actual pick count** are different signals. Do not count cancelled lines as completed picks or equate a line with one physical pick when it was split across bins/tasks. Date-only orders remain date-grain demand observations; do not fabricate midnight UTC timestamps or infer intra-day demand from them.

### `pick_events.csv` (actual task records)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `pick_event_id` | id | R | — | PK within this import; stable canonical task/outcome ID, namespaced if source IDs repeat. A later snapshot may carry a corrected version of the same source task with auditable supersession; do not count both versions. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals order and location warehouse. |
| `order_line_id` | id | O | — | FK → `order_lines` when the WMS records it; **many events per line** allowed. Blank permits task-level history, but excludes line reconciliation. If supplied, line SKU and order warehouse must match. |
| `sku_id` | id | R | — | FK → `skus`; equals referenced line SKU when `order_line_id` is present. |
| `location_id` | id | C | — | Required for a completed pick with positive quantity; FK → current `locations` **or** `historical_location_refs`, same warehouse. A short/failed task may lack a bin if none was visited; historical-only IDs cannot support current-layout route calibration. |
| `lot_id` | text | O | — | When present for a tracked SKU, FK → `sku_lots.(sku_id, lot_id)`; blank for untracked SKU. Historical WMS events lacking lot identity may be imported for aggregate work metrics but cannot support lot-level/FEFO validation. |
| `picked_qty` | decimal | R | SKU base units | ≥0; >0 for completed task; a short/failed task may be zero. |
| `event_at` | timestamp | R | UTC | Actual task outcome time in `[history_from, history_to)`; ≥ parent `ordered_at` when exact time exists, otherwise its warehouse-local date is ≥ parent `ordered_on`. |
| `started_at` | timestamp | O | UTC | If present, `started_at ≤ event_at` and ≥ exact parent `ordered_at` when available; missing means task duration cannot be learned. |
| `status` | enum | R | — | `completed`, `short`, `failed`, `cancelled`. Source reversals/corrections must be resolved into a nonduplicated canonical event history with an auditable source linkage before import; never count both original and replacement. |
| `wave_id`, `route_id` | id | O | — | Correlate multiple picks into a real walk route; canonical `route_id` identifies one route **instance** within a warehouse, not a reused route template/code. At least one is needed for batch-route calibration. |
| `route_sequence` | integer | O | step | ≥1; when present requires `route_id` and is unique within `(warehouse_id, route_id)`. |
| `picker_pseudonym` | text | O | — | Stable pseudonym if labor analysis is approved; no worker PII needed. |
| `distance_m` | decimal | O | m | ≥0; only when sourced from a real measurement or separately documented WMS estimate. |
| `distance_method` | enum | C | — | Required with `distance_m`: `measured`, `telemetry`, or `wms_estimate`; never relabel a formula as measured. |

Sum of valid completed `picked_qty` by order line reconciles with `fulfilled_qty` **only when** all events carry line IDs, the extract includes the whole line lifecycle and the source declares event coverage complete; otherwise report coverage and do not claim exact reconciliation. Partial picks across bins/lots are expected. Duplicate canonical event IDs **within a batch** are rejected. Across batches, the same ID may reappear unchanged or as a source-verified correction/superseding state; lineage identifies the version and prevents double counting. A missing `lot_id` for a tracked SKU prevents lot/FEFO validation of that event; missing `route_id`/`started_at` limits route/time calibration rather than creating an observed route/time. Historical event lot/bin links do not prove historical inventory state.

### `replenishment_events.csv` (actual tasks, not aggregate counts)

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `replenishment_event_id` | id | R | — | PK within this import; stable namespaced WMS task ID. A later extract may show the same task at a later status; use source version/time lineage and never count both states as separate completed tasks. |
| `warehouse_id` | id | R | — | FK → `warehouses`; same as source/destination bins. |
| `sku_id` | id | R | — | FK → `skus`. |
| `lot_id` | text | O | — | When present for a tracked SKU, FK → `sku_lots.(sku_id, lot_id)`; blank for untracked SKU. Missing historical task lot limits lot-specific replenishment analysis, not aggregate workload analysis. |
| `source_location_id` | id | O | — | FK → current `locations` or `historical_location_refs` when stock came from a bin; same warehouse. Blank for an external source or a WMS task that omits source location. |
| `source_node_id` | id | O | — | FK → `aisle_nodes` for a known dock/staging source, same warehouse. At least one source location/node is needed for **route** analysis of a completed task; if both are present and the location is current, the node must be a valid access point for it. A historical-only location does not validate a current graph node. |
| `destination_location_id` | id | C | — | Required for a completed task; FK → current `locations` or `historical_location_refs` when present, same warehouse. A cancelled/failed request without a resolved destination may leave it blank. A historical-only destination cannot prove current pick-face eligibility; such tasks remain in aggregate refill history but not current-bin route calibration. |
| `moved_qty` | decimal | R | SKU base units | ≥0; >0 when completed; count actual moved units, not an estimated task count. |
| `event_at` | timestamp | R | UTC | Actual status observation/outcome time in `[history_from, history_to)` and ≤ `snapshot_at`; for completed tasks equals `completed_at`. |
| `requested_at` | timestamp | O | UTC | If known, ≤ `event_at`; may predate `history_from`. Missing request time prevents request-to-completion lead-time analysis. |
| `started_at` | timestamp | O | UTC | If known, `requested_at ≤ started_at ≤ event_at` when a request time exists; otherwise `started_at ≤ event_at`. |
| `completed_at` | timestamp | C | UTC | Required only when `status=completed` and equals `event_at`; blank for other statuses. Must be ≥ any supplied start/request time. |
| `status` | enum | R | — | `requested`, `in_progress`, `completed`, `cancelled`, `failed`; no simulated aggregate rows. |
| `method_code` | text | O | — | Site-controlled `manual`, `forklift`, etc.; map before using equipment constraints. |
| `worker_pseudonym` | text | O | — | Optional approved labor-analysis ID. |
| `distance_m` | decimal | O | m | ≥0; method given by `distance_method`, not silently assumed to be pick travel. |
| `distance_method` | enum | C | — | Required with `distance_m`: `measured`, `telemetry` or `wms_estimate`; blank if no distance. |

Historical completed events calibrate refill rates; task duration is learned only when start/completion timestamps are present. If a WMS records an external source without an aisle node, the task may be kept for demand/replenishment counts but cannot enter route modeling until an approved source node is mapped. A complete inventory snapshot must reconcile with its own WMS source; reconstructing current inventory from incomplete event history is not permitted.

### Warehouse aisle layout: `aisle_nodes.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `node_id` | id | R | — | PK; stable routing point. |
| `warehouse_id` | id | R | — | FK → `warehouses`. |
| `node_type` | enum | R | — | `depot`, `packing`, `intersection`, `aisle_end`, `location_access`, `staging`; at least one active depot/packing node per route-eligible active warehouse. |
| `x_m`, `y_m` | decimal | O | m | Both or neither; surveyed floor-plan coordinates in documented common origin. Required if edge lengths are derived geometrically. |
| `floor_level` | integer | R | level | Signed whole number, allowing basements; all nodes in a warehouse use one documented level origin. Cross-level movement only through explicit edges. |
| `status` | enum | R | — | `active`/`inactive`; inactive node unavailable for routing. |

### Warehouse aisle layout: `aisle_edges.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `edge_id` | id | R | — | PK. |
| `warehouse_id` | id | R | — | FK → `warehouses`; both endpoint nodes same warehouse. |
| `from_node_id`, `to_node_id` | id | R | — | FK → `aisle_nodes`; different endpoints. |
| `length_m` | decimal | R | m | >0, measured walkable-path length including turns/ramps as appropriate; not straight-line distance unless path really is straight. |
| `bidirectional` | boolean | R | — | False means only from → to is traversable. |
| `travel_mode` | enum | R | — | `picker`, `forklift`, `both`; route calculation filters by actual mode. |
| `status` | enum | R | — | `active`/`blocked`/`inactive` at manifest `snapshot_at`; closures require new snapshot or dated closure feed. |
| `level_change` | integer | O | levels | Signed number; must equal endpoint level difference when present. |

### Warehouse aisle layout: `location_access.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `access_id` | id | R | — | PK. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals location/node warehouse. |
| `location_id` | id | R | — | FK → `locations`. |
| `node_id` | id | R | — | FK → `aisle_nodes`, same warehouse; for a candidate pick face, reachable from its configured/actual route departure node under the stated travel mode. An unrelated depot need not reach it. |
| `walk_offset_m` | decimal | R | m | ≥0, measured travel distance from graph node to physical access point for this row's `travel_mode`. Use separate access rows if picker and forklift offsets differ. |
| `travel_mode` | enum | R | — | `picker`, `forklift`, `both`; must be supported by connected edges. |
| `status` | enum | R | — | `active`/`inactive`; each **candidate** pick face requires at least one active picker access. Historical/inactive/blocked bins may remain without current access. |

Several access points per bin are allowed (for example front/back aisle). Routing uses the shortest valid path over active nodes/edges plus access offsets. Aisle/bay labels cannot replace this graph. A disconnected bin is ineligible, not assigned an invented distance.

Earlier Slotwise exports used `access_offset_m` for this field. The importer accepts that legacy header and normalizes it to `walk_offset_m`; a file containing both headers is invalid. New exports use `walk_offset_m`.

### Hazard policy: `location_hazard_permissions.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `permission_id` | id | R | — | PK; unique `(location_id, hazard_group)` at snapshot. |
| `warehouse_id` | id | R | — | FK → `warehouses`; equals location warehouse. |
| `location_id` | id | R | — | FK → `locations`. |
| `hazard_group` | text | R | — | Approved group from site policy, including explicit `none`; absence means **not permitted**. |

### Hazard policy: `hazard_segregation_rules.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `rule_id` | id | R | — | PK; one active row per unordered group pair and warehouse. |
| `warehouse_id` | id | R | — | FK → `warehouses`. |
| `hazard_group_a`, `hazard_group_b` | text | R | — | Approved groups, including `none`; unordered pair unique at snapshot. Include self-pairs when a group appears in multiple SKUs/bins. |
| `allow_same_location` | boolean | R | — | Explicit permission for pair in one bin; `false` blocks co-location regardless of generic co-storage rule. |
| `minimum_clearance_m` | decimal | R | m | ≥0 physical boundary-to-boundary separation between **different** bins; 0 explicitly means none. Positive value also makes same-bin placement invalid. Never use picker-walking distance. |
| `prohibit_same_zone` | boolean | R | — | If true, pair cannot occupy bins in the same zone, irrespective of clearance. |
| `barrier_required` | boolean | R | — | If true, approved physical barrier between bins is required. |
| `required_barrier_code` | text | C | — | Nonempty site-certified barrier rating when `barrier_required=true`; blank otherwise. |
| `approval_ref` | text | R | — | Nonempty safety-owner approval/evidence reference. |
| `effective_from` | timestamp | R | UTC | Start inclusive; active rule must cover manifest `snapshot_at`. |
| `effective_to` | timestamp | O | UTC | End exclusive; > start if present; no overlapping rule for a pair. |

Normalize `(hazard_group_a,hazard_group_b)` as an unordered key; reversed duplicate active rows are conflicts. For all hazard groups represented by existing or proposed stock in a warehouse, require an approved rule for each relevant unordered pair, **including pairs with `none`**. Missing rules mean unknown safety, not permission. `allow_same_location=true` is inconsistent with `minimum_clearance_m>0`, `prohibit_same_zone=true` or `barrier_required=true` and is rejected. Every rule is evaluated against **all existing and post-move stock**, including held/expired material until physically removed. Generic group codes and walkable-route distance cannot substitute for a facility's regulatory and safety policy.

### Hazard policy: `hazard_separation_evidence.csv`

| Field | Type | Req | Unit | Validation / relationship |
| --- | --- | --- | --- | --- |
| `evidence_id` | id | R | — | PK; unique unordered location pair at snapshot. |
| `warehouse_id` | id | R | — | FK → `warehouses`; both bins must be in that warehouse. |
| `location_a_id`, `location_b_id` | id | R | — | FK → distinct `locations`; order-independent pair. |
| `physical_clearance_m` | decimal | R | m | ≥0, surveyed shortest relevant **physical** separation between bin boundaries, using the safety policy's approved measurement method. |
| `barrier_code` | text | O | — | Certified barrier rating between the bins; must meet `required_barrier_code` under the site's approved rating comparison table. Unknown/unordered codes cannot satisfy a barrier requirement. |
| `surveyed_at` | timestamp | R | UTC | ≤ manifest `snapshot_at`; evidence corresponds to current layout. |
| `valid_until` | timestamp | O | UTC | If present, > manifest `snapshot_at`; expired evidence cannot be used. |
| `approval_ref` | text | R | — | Nonempty site safety/evidence reference. |

For a current or candidate **different-bin** hazard pair subject to positive clearance or barrier requirement, matching current evidence is required; absence blocks that candidate. `prohibit_same_zone` is checked from zone IDs without a distance calculation. Same-bin compatibility is checked from `allow_same_location`, generic co-storage and location permissions. Evidence need only cover evaluated/currently occupied pairs, not every possible pair of empty bins, but a new candidate cannot be accepted without it. Hazard separation is physical/regulatory separation, never the shortest aisle route. If a site cannot provide approved policy and required evidence, hazardous placements remain ineligible.

## 3. Cross-file integrity and import validation

1. **Stage and map.** Keep raw WMS files read-only. A versioned adapter maps field names, stable IDs, enum/status meanings, unit-of-measure conversions and source time zone. Record source file, row and transform for every canonical row. Unmapped/ambiguous values go to a quarantine report, never to guessed defaults.
2. **Parse and schema check.** Verify manifest, SHA-256s, exact headers/types, required/conditional fields, decimal precision, positive physical limits, enum registries, unique keys and UTC timestamp formats. Resolve every source quantity through one effective SKU-specific UOM rule or a measured base quantity; reject ambiguous, overlapping, rounded or unmeasured variable conversions. Reject missing required snapshot files or unproven as-of consistency; optional history files may be absent only with the resulting capability limits reported.
3. **Relational check.** Resolve every **supplied** FK, tenant scope and same-warehouse join. Assert effective SKU/package/lot coverage for current stock and candidates, package identity and count, assignment/inventory location references, conditional order-line/pick linkage, route node/edge/access linkage and no overlapping effective versions within one scope. Historical events without a line/lot/bin link remain usable only for metrics that do not depend on that link. Require one physical count per stock package, including non-pickable inventory; prevent duplicate pallet/case representations. Do not require unique SKU or location in assignments; enforce the assignment composite key and each bin's distinct-SKU/lot limits instead.
4. **Physical/safety check.** For each bin's **post-move** stock, sum `handling_unit_count × gross_weight_kg` and `handling_unit_count × outer_length_cm × outer_width_cm × outer_height_cm` over actual package profiles; compare with total load and usable outer-volume limits. Opened packs conservatively retain full-package gross weight and footprint. Check individual orientation/fit, stack layers and supported top load; use a certified single-profile capacity for a sole package profile or a joint packing plan/validated solver for mixtures. Check temperature interval containment and controlled-storage certification, co-storage permission, lot mixing, site-approved hazard permissions and pairwise physical separation/barriers, zone/facility status and reachable access. Aggregate limits alone never prove fit. Existing over-capacity/unsafe bins become explicit incidents; affected recommendations are blocked. Do not erase real stock to make a proposal feasible.
5. **Temporal/operational check.** Prove transactional, replayed or verified-quiescent consistency across every snapshot file and source at the **same** `snapshot_at`; reconcile physical packages/quantities with WMS snapshot control totals by warehouse, SKU, location, lot and status where available. Enforce lot expiry/disposition and event-time rule coverage. Validate event sequence, stable IDs, multiple partial picks, appropriately adjusted fulfilled amounts and replenishment source/destination flow. Event extracts in `[history_from,history_to)` do not reconstruct older stock or prove whole-line reconciliation; claim those only with explicit full coverage. Exclude cancelled/failed events from observed velocity while retaining them for exception analysis. Missing task start/route IDs limit time/route calibration; never simulate observations.
6. **Route check.** Verify picker graph connectivity from the configured default pick node or route-specific actual departure node to each candidate pick face, respecting directed edges, travel mode and edge lengths; an unrelated depot does not have to reach every face. Compare calculated distances with genuinely measured/telemetry task records when available; report bias by zone, batch size and shift. Do not certify modeled distance solely by recomputing it.
7. **Publish atomically by warehouse.** Produce a structured validation report: row counts, accepted/quarantined/rejected rows by reason, coverage per warehouse/SKU/bin, missing data, freshness, total load/volume exceptions, unresolved policies, route reachability and source lineage. A warehouse snapshot is `accepted_for_recommendations` only when its identity, as-of consistency and required physical joins pass. An unrelated warehouse in the same import may be accepted independently; publish each warehouse's validated files together, never a mix of old and new rows. Existing unsafe bins or missing candidate-specific certificates remain visible incidents and exclude affected moves; they do not erase valid stock or automatically invalidate unrelated bins. If a warehouse-wide gate fails, retain that warehouse's previous accepted snapshot. Keep the synthetic demo separate and visibly labelled.

**Capability gates:** `demand_only` supports daily or timestamped demand analysis, never bin recommendations. `full_snapshot` with physical masters/inventory/assignments/graph but no historical demand can support capacity diagnostics and user-specified what-if quantities, not demand-based slot ranking. With historical orders or picks it can support constrained *theoretical* routes after policy approval, but not calibrated pick-time or measured savings claims. Real pick/replenishment events support only the calibration/backtesting metrics for which timestamp, bin, line, lot, route and coverage fields are actually present; task-level events without line IDs remain useful for aggregate workload. Every run records accepted `dataset_id`, warehouse ID, mapping/policy versions, objective configuration and snapshot time. A 90–95% accuracy claim requires a separately defined held-out metric and real observed outcomes.

## 4. Mapping of today's files to the contract

`Current demo` below means fields in `data/starter/*.csv` / `data/full/*.csv`; these are synthetic and may be **structurally mapped for development only**. `King's Pantry` means the supplied `supply2` files. The standalone `supply_chain_data.csv` is a different, unjoinable SKU universe.

| Canonical file | Current demo field candidates | King's Pantry field candidates | Standalone supply-chain CSV / missing real export |
| --- | --- | --- | --- |
| `warehouses.csv` | `warehouse_id`, `name`, `status`; no timezone/depot graph node. | No warehouse master. `sales_channel` is **not** a warehouse. | `Location` is city, not facility/bin. **Need WMS facility master and depot point.** |
| `zones.csv` | `zone_id`, `warehouse_id`, `zone_type`, `status`. | None. | **Need facility zone master.** |
| `locations.csv` | `location_id`, `zone_id`, `warehouse_id`, dimensions, `capacity_cm3`, `max_weight_kg`, `temperature`, `hazard_class`, `blocked`, status; nominal demo values, no measured stack/lot-mixing limits. | None. | No bin fields. **Need measured clear dimensions, total load, certified temperature bounds, availability and sharing/lot limits.** |
| `skus.csv` | `sku_id`, `name`, `category`, status; current `warehouse_id` is a demo home-warehouse restriction, not a canonical SKU property. | `product.product_id → sku_id`, `product_name → name`, taxonomy → category, active/stock/discontinued flags after documented mapping. | `SKU`/`Product type` can form a *separate* product catalogue; IDs do not join King's Pantry. |
| `sku_profiles.csv` | `length_cm`, `width_cm`, `height_cm`, `weight_kg`, `temperature`, `hazard_class`, effective dates; no approved orientation/stack or site safety policy. | `unit_size` is a retail label, **not** dimensions/weight/temp/hazard. | No physical attributes. **Need certified storage rules; bare-unit measurements only when that form is used.** |
| `sku_uom_conversions.csv` | Demo demand/stock is generated in an implicit unit; no approved effective-dated conversions. | `product.unit_size` is a retail label; order detail quantity alone does not establish case/each conversion. | **Need source UOM on each relevant quantity and approved SKU-specific conversion or actual measured base quantity.** |
| `sku_packaging_profiles.csv` | SKU dimensions and weight do not describe stored cases/pallets, package counts or stack/load limits. | None. | **Need measured physical package type, units/package, outside size, gross weight and stack rules.** |
| `sku_lots.csv` | No actual lot, serial, expiry or disposition master. | None. | **Need authoritative tracked-lot/serial master and expiry/release state where applicable.** |
| `inventory_by_location.csv` | None; `slot_assignments` is not stock quantity. | None. | `Stock levels` has no bin, timestamp or matching SKU ID. **Need complete WMS stock-by-bin/lot/status/package snapshot with reserved/held quantities and physical package counts.** |
| `location_pack_capacities.csv` | Demo bin volume is not a certified per-package fit/stack quantity. | None. | **Need approved bin/package capacity evidence or a validated packing method.** |
| `slot_assignments.csv` | `sku_id`, `location_id`, `status`, validity dates, `run_id`; generated baseline allows one SKU/bin. | None. | **Need actual current pick/reserve assignments, including multiple bins per SKU.** |
| `historical_location_refs.csv` | No retired-bin identity export. | None. | **Need historical bin→warehouse reference only when task events mention bins absent from the current location master.** |
| `orders.csv` | `order_id`, `warehouse_id`, `ordered_at`, status. | `sales_order_id`, `order_date`, `order_status`, `sales_channel` can map after status/time-zone/facility rules; date can map to `ordered_on` for daily demand but lacks time of day and fulfillment warehouse. | No dated orders. **Need actual fulfillment facility; exact timestamps for within-day analysis.** |
| `order_lines.csv` | `order_line_id`, `order_id`, `sku_id`, `quantity`, `line_number`; quantity's physical/source UOM is implicit. | `sales_order_detail_id`, `sales_order_id`, `product_id`, `quantity_ordered`, `line_status` can map after SKU/UOM/status policy. All observed line→order/product IDs resolve; 47,156 lines over 92 days. | `Number of products sold` is undated aggregate, not a line. **Need source quantity UOM/conversion lineage.** |
| `pick_events.csv` | Generated `pick_event_id`, line, location, `picked_at`, formula distance; **not observed**. | None. | **Need scanned task events, actual bin, quantities and timestamps; route/wave IDs for batch calibration.** |
| `replenishment_events.csv` | Current `replenishments.csv` has one simulated aggregate row/SKU, not a task event. | None; vendor file contains no receipts/tasks. | `Lead time`/`Order quantities` are not refill events. **Need source/destination, quantity and task times.** |
| `aisle_nodes.csv`, `aisle_edges.csv`, `location_access.csv` | Only integer aisle/bay labels; no walkable graph. | None. | `Routes` describes transport labels, not internal aisles. **Need surveyed aisle layout and bin access mapping.** |
| `co_storage_rules.csv`, `shared_bin_plans.csv`, `shared_bin_plan_items.csv` | Demo assumes one SKU/bin; no approved pair compatibility or joint pack evidence. | None. | **Need site-approved product co-storage and joint packing evidence for any shared-bin proposal.** |
| `location_hazard_permissions.csv`, `hazard_segregation_rules.csv`, `hazard_separation_evidence.csv` | Simple `hazard_class` exact-match labels; no certified site policy or physical clearance. | Product category/brand cannot infer hazard. | **Need facility-approved permissions, group-pair rules and measured clearance/barrier evidence where rules require it.** |

`brand.csv`, `vendor.csv`, `customer.csv`, `sales_channel_lookup.csv`, `docs_category_weighting.csv`, `docs_customer_mix.csv`, `docs_discount_logic.csv`, `docs_tax_logic.csv` and the Power BI theme are not physical slotting masters. Brand/vendor/channel may be optional analytics enrichments. Customer personal fields, tax and discount values are unnecessary for canonical slotting. The King's Pantry customer IDs include 20 conflicting duplicate IDs; do not use customer joins without resolution. The standalone 100-row supply-chain file uses `SKU0`-style identifiers and city-level locations and must not be merged with King's Pantry or the demo by apparent column similarity.

No supplied Kaggle file provides a real warehouse-to-bin-to-SKU current-state link, a measured product footprint/load, or an actual pick route. These gaps cannot be filled by synthetic defaults if results are to guide real stock movement.

## 5. Minimum first-warehouse export for meaningful recommendations

The first operational pilot needs **one named warehouse and one proven coherent as-of snapshot**, plus a representative period of real workload. Each contributing WMS/ERP export must retain its raw IDs, source UOM, capture time and control totals; use one source transaction, complete replay or verified quiescent interval to establish the common `snapshot_at`.

1. **Physical master:** warehouse/zone/bin IDs and statuses; measured clear bin dimensions, usable volume and total load limit; pick/reserve role, certified operating temperature range, SKU/lot sharing limit and blocked bins; surveyed walkable aisle graph with depot/packing node and access node for every candidate pick bin.
2. **SKU and package master:** stable SKU IDs, base UOM, approved effective SKU-specific conversions for non-base source quantities (or actual measured base quantities for variable conversions), and every **used stock package's** units/pack, outside dimensions, gross full-pack weight, allowed orientations, stack layers and supported top load. Bare-base-unit dimensions are needed only if bare units are stored/proposed. Include certified bin/package capacity or a validated packing method, storage temperature bounds, hazard/co-storage groups and site-approved hazard permissions/segregation. For mixed-package bins, provide joint packing plans or a validated joint packing method; mixed-SKU bins also need explicit co-storage rules. Where hazard policy requires separation, provide surveyed physical clearance/barrier evidence.
3. **Current state:** complete inventory **by warehouse, bin, SKU, physical package, lot/serial and stock status** with actual package count, base quantity, reserved/held quantity, authoritative lot expiry/disposition where tracked, and current pick/reserve assignments including multiple bins per SKU. Reconcile package and quantity totals with the source WMS at the common snapshot time. Stock in quarantine, expired state or a bin planned for removal still consumes physical capacity until actually moved.
4. **Demand and operations:** historical order lines with actual fulfillment warehouse, source UOM/conversion, timestamps, statuses, SKU and quantity; completed/short pick events with actual bin, lot where tracked, quantity and time, preferably wave/route and task start; replenishment tasks with source, destination, lot where tracked, moved quantity and time. Declare event window and lifecycle coverage. A useful calibration window must cover ordinary and peak patterns; no universal number of weeks can be promised before examining seasonality and volume.

With items 1–3 and real order history, Slotwise could generate **physically constrained, route-based proposals** after the optimizer and validator are upgraded. Actual pick and replenishment events in item 4 are required to calibrate time/refill predictions and verify savings against the real current layout. Missing certified package/bin fit, unresolved stock counts/lots, unsafe co-storage/hazard evidence or an unproven common snapshot blocks the affected moves; sales-only data supports demand analysis, not operational slot moves.

## 6. Explicit v1 boundaries

- A physical handling unit spanning several SKU/lot/serial rows (for example a mixed-lot pallet or a case of individually serialized units) cannot be safely represented by counting that same package once on **each** inventory row. Preserve its source records for diagnostics, but do not calculate occupancy or propose a move for its bin under this v1 schema. A future handling-unit hierarchy must represent the package once and link its contents. Individually packaged serials and separately counted lot packages are supported now.
- Current aisle geometry and current lot disposition do not reconstruct historical routes, stock positions or safety status. Historical task metrics use only observations actually supplied; backtesting past layouts requires time-appropriate layout, inventory and rule evidence.
- Certified pack capacities, joint packing plans, temperature limits and hazard clearances remain facility evidence. The CSV contract records and checks them; it cannot infer them from sales data, product category, aisle labels or aggregate volume.
