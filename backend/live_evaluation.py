"""Compare a proposal with independently supplied post-deployment observations."""
import csv
import io
from datetime import datetime, timezone


def evaluate(result: dict, content: bytes) -> dict:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        required = {"sku_id", "actual_location_id", "observed_at"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("Outcome CSV needs sku_id, actual_location_id, observed_at; measured_round_trip_m is optional.")
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise ValueError(f"Invalid outcome CSV: {exc}") from exc
    if not rows:
        raise ValueError("Outcome CSV has no observation rows.")
    proposals = {row["sku_id"]: row for row in result["recommendations"]}
    seen = set()
    matched = 0
    move_matched = 0
    evaluated_moves = 0
    route_errors = []
    snapshot = datetime.fromisoformat(result["snapshot_at"])
    for number, row in enumerate(rows, 2):
        sku = (row.get("sku_id") or "").strip()
        location = (row.get("actual_location_id") or "").strip()
        if not sku or not location or sku not in proposals or sku in seen:
            raise ValueError(f"Outcome row {number}: SKU/location is blank, unknown, or duplicated.")
        seen.add(sku)
        try:
            observed = datetime.fromisoformat((row.get("observed_at") or "").replace("Z", "+00:00"))
            if observed.tzinfo is None or observed.astimezone(timezone.utc) <= snapshot:
                raise ValueError()
        except ValueError as exc:
            raise ValueError(f"Outcome row {number}: observed_at must be after the analysis snapshot and include a timezone.") from exc
        proposal = proposals[sku]
        agrees = proposal["proposed_location_id"] == location
        matched += agrees
        if proposal["decision"] == "move":
            evaluated_moves += 1
            move_matched += agrees
        measured = (row.get("measured_round_trip_m") or "").strip()
        if measured:
            try:
                value = float(measured)
                if not 0 <= value < float("inf"):
                    raise ValueError()
            except ValueError as exc:
                raise ValueError(f"Outcome row {number}: measured_round_trip_m must be a finite nonnegative number.") from exc
            if proposal["proposed_round_trip_m"] is not None and agrees:
                route_errors.append(abs(value - proposal["proposed_round_trip_m"]))
    return {"observed_skus": len(rows), "proposed_skus": len(proposals),
            "coverage_pct": round(100 * len(rows) / len(proposals), 2) if proposals else None,
            "placement_agreement_pct": round(100 * matched / len(rows), 2),
            "evaluated_moves": evaluated_moves,
            "move_agreement_pct": round(100 * move_matched / evaluated_moves, 2) if evaluated_moves else None,
            "measured_routes": len(route_errors),
            "route_mae_m": round(sum(route_errors) / len(route_errors), 2) if route_errors else None,
            "note": "Placement agreement measures adoption of proposed bins, not optimality or model accuracy. Route MAE uses only observed SKUs in their proposed bin."}
