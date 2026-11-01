# Seven-minute demo

1. **Problem (30 seconds):** Explain that fast-moving SKUs should be close to picking, while size, weight, temperature, hazards, and slot availability are hard constraints.
2. **Data (1 minute):** Show the starter manifest and the warehouses/locations/SKUs counts. Explain that the full command generates 20 warehouses, 10,000 locations, 5,000 SKUs and 100,000 lines.
3. **Decision (2 minutes):** Open Recommendations. Pick a high-frequency SKU and show current/proposed slot, route change, score, and reason. Emphasize that the row is a proposal, not a physical relocation.
4. **Exceptions (1 minute):** Show all five labelled scenarios and the 5/5 detector result. Open `ground_truth.csv` to show labels are separate from optimizer inputs.
5. **Impact (1 minute):** Show simulated distance and time comparison on identical orders. State that full-scale replenishment rises, illustrating a tradeoff to improve in a later multi-objective algorithm.
6. **Engineering (1.5 minutes):** Show `python -m backend.validate --dataset data/full`, `python -m backend.reproducibility --scale starter`, and the data dictionary. Point to the fixed seed, checksum manifest, immutable run folders, tests, and SQL/JSON export.

The optional demand forecast is separate from the placement rule. If used, compare its holdout MAE to the rolling-average baseline before claiming value.
