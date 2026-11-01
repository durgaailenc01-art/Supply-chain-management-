# Business rules

| Rule | Decision |
| --- | --- |
| Warehouse | SKU and slot must share a warehouse. |
| Size | Each SKU dimension must fit the corresponding location dimension; rotation is not modeled. |
| Load | SKU weight and volume must not exceed location capacity. |
| Temperature | `ambient`, `chilled`, or `frozen` must exactly match the location class. |
| Hazard | `none` can use `none` slots; `flammable` and `corrosive` require the same dedicated class. |
| Availability | Blocked aisles and inactive locations are ineligible. |
| Exclusivity | At most one active or proposed SKU per pick location in a scenario. |

Velocity is derived from order-line frequency, never assigned independently. The generator uses a skewed demand distribution and a deterministic seasonal boost on every tenth SKU to create fast/slow movers and peak shifts. Oversized SKUs are assigned first, then the optimizer sorts by pick frequency and places each SKU in the nearest eligible unreserved slot; ties favor larger unit capacity, then location ID. The baseline picks a valid compatible slot with a fixed random seed, without using demand. This provides a fair demand-independent comparison rather than constructing an artificially worst baseline.

The displayed explanation score is `1000 / (1 + route distance metres) + min(unit capacity, 100) / 10000`, rounded to four decimals. It summarizes the chosen location after hard filtering. Matching uses the exact distance and capacity tie break, so a rounded display score is not used to reserve locations.

Exception fixtures intentionally violate one rule each. The detector checks the fixture data without contaminating optimizer features. Ground truth is an independent expected-label file; detection output can be compared against it. Replenishment labor capacity is 8 tasks per worker per shift in the fixture scenario.
