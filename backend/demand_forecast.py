"""Optional ML demand signal; hard slotting constraints remain deterministic."""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from .engine import read_csv
from .generate import SEED


def forecast(dataset: Path, *, top_n: int = 20) -> dict:
    orders = pd.DataFrame(read_csv(dataset / "orders.csv"))[["order_id", "ordered_at"]]
    lines = pd.DataFrame(read_csv(dataset / "order_lines.csv"))[["order_id", "sku_id"]]
    top = lines["sku_id"].value_counts().head(top_n).index.tolist()
    joined = lines[lines["sku_id"].isin(top)].merge(orders, on="order_id", validate="many_to_one")
    joined["day"] = pd.to_datetime(joined["ordered_at"], utc=True).dt.floor("D")
    counts = joined.groupby(["sku_id", "day"]).size().rename("picks")
    first_day = pd.to_datetime(orders["ordered_at"], utc=True).min().floor("D")
    last_day = pd.to_datetime(orders["ordered_at"], utc=True).max().floor("D")
    days = pd.date_range(first_day, last_day, freq="D", tz="UTC")
    rows = []
    histories = {}
    for rank, sku_id in enumerate(top):
        history = counts.loc[sku_id].reindex(days, fill_value=0).astype(float).to_numpy()
        histories[sku_id] = history.tolist()
        for day in range(7, len(history)):
            rows.append((rank, day, (first_day.to_pydatetime() + timedelta(days=int(day))).weekday(),
                         history[day-1], history[day-7], float(np.mean(history[day-7:day])), history[day]))
    frame = pd.DataFrame(rows, columns=["sku_rank", "day_index", "weekday", "lag_1", "lag_7", "rolling_7", "target"])
    if frame.empty or frame["day_index"].max() < 21:
        raise ValueError("At least 22 days of order history are required")
    cutoff = int(frame["day_index"].max()) - 13
    features = ["sku_rank", "day_index", "weekday", "lag_1", "lag_7", "rolling_7"]
    train, test = frame[frame["day_index"] < cutoff], frame[frame["day_index"] >= cutoff]
    model = RandomForestRegressor(n_estimators=80, max_depth=10, min_samples_leaf=3, random_state=SEED, n_jobs=1)
    model.fit(train[features], train["target"])
    predicted = np.clip(model.predict(test[features]), 0, None)
    baseline = test["rolling_7"].to_numpy()
    future = []
    for rank, sku_id in enumerate(top):
        history = list(histories[sku_id])
        for step in range(7):
            day = len(history)
            values = pd.DataFrame([[rank, day, (first_day.to_pydatetime() + timedelta(days=int(day))).weekday(),
                                    history[-1], history[-7], float(np.mean(history[-7:]))]], columns=features)
            estimate = max(0.0, float(model.predict(values)[0]))
            history.append(estimate)
        future.append({"sku_id": sku_id, "historical_7_day_picks": round(sum(histories[sku_id][-7:]), 1),
                       "forecast_7_day_picks": round(sum(history[-7:]), 1)})
    return {"model": "RandomForestRegressor", "seed": SEED, "training_days": cutoff,
            "holdout_days": 14, "holdout_mae": round(mean_absolute_error(test["target"], predicted), 3),
            "rolling_mean_baseline_mae": round(mean_absolute_error(test["target"], baseline), 3),
            "forecast": future}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/starter"))
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(forecast(args.dataset, top_n=args.top_n), indent=2))
