# Data model and lifecycle

`Warehouse 1:N Zone 1:N Location`; `SKU 1:1 SKUProfile`; `Warehouse 1:N Order 1:N OrderLine`; `SKU 1:N OrderLine`; `OrderLine 1:0..1 PickEvent`; `SKU 1:N Replenishment`; `Location 1:N Replenishment`; `SKU 1:N SlotAssignment`; `Location 1:N SlotAssignment`. `SlotAssignment` is the effective-dated bridge between SKU and Location. A SKU belongs to one home warehouse in this demo. Each active SKU has one active pick slot and each active slot contains at most one SKU. Historical assignment rows may repeat either ID.

Master data precedes transactions. `OrderLine` joins to both `Order` and `SKU`, and its SKU warehouse must match the order warehouse. Baseline assignments are `active`; optimization outputs are `proposed` with a run ID and do not silently move inventory. A future approval action would close the old assignment (`valid_to`) and append a new active row. Pick and replenishment rows are immutable events. Exception fixtures live apart from baseline inputs with an `issue_type` label only in `ground_truth.csv`.

All IDs are immutable strings. Timestamps are UTC ISO-8601. Units: dimensions cm, weights kg, volume cm³, travel distance metres, time seconds. CSV is the canonical storage for this single-user demo. API output is JSON. No external dataset or personal information is used.
