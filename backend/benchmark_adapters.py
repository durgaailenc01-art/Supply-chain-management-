"""Lossless, partial projections of public benchmarks into canonical CSV columns.

These records are intentionally not importable as operational snapshots: fields
absent from the publication remain blank. No capacity or safety facts are inferred.
"""
from __future__ import annotations

import csv
import json
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from .canonical_schema import TABLES


def _row(table: str, **known: str) -> dict[str, str]:
    fields = {column.name for column in TABLES[table]}
    if set(known) - fields:
        raise ValueError(f"Unknown canonical fields: {set(known) - fields}")
    return {column.name: known.get(column.name, "") for column in TABLES[table]}


def footwear_projection(folder: Path, *, order_limit: int | None = None) -> dict:
    """Map source IDs, product codes, dates and coordinates; preserve unknowns."""
    folder = Path(folder)
    with (folder / "Product.csv").open(encoding="utf-8-sig", newline="") as stream:
        products = list(csv.DictReader(stream, delimiter=";"))
    with (folder / "Storage_Location.csv").open(encoding="utf-8-sig", newline="") as stream:
        locations = list(csv.DictReader(stream))
    skus = [_row("skus", sku_id=product["Reference"].strip(), category_code=product["ABCCOD"].strip()) for product in products]
    bins = [_row("locations", location_id=location["originalLocation"].strip()) for location in locations]
    nodes = [_row("aisle_nodes", node_id=location["originalLocation"].strip(),
                  x_m=location["x"].strip(), y_m=location["y"].strip()) for location in locations]
    orders, lines, seen = [], [], set()
    with (folder / "Customer_Order.csv").open(encoding="utf-8-sig", newline="") as stream:
        for index, source in enumerate(csv.DictReader(stream, delimiter=";"), 1):
            order_id = source["orderNumber"].strip()
            if order_id not in seen:
                if order_limit is not None and len(orders) >= order_limit:
                    break
                seen.add(order_id)
                # The source timestamp is naive. Only its calendar date is safe to map.
                date = datetime.strptime(source["creationDate"].strip(), "%d/%m/%Y %H:%M").date().isoformat()
                orders.append(_row("orders", order_id=order_id, ordered_on=date))
            lines.append(_row("order_lines", order_line_id=f"source-row-{index}", order_id=order_id,
                              sku_id=source["Reference"].strip(), ordered_qty=source["quantity (units)"].strip()))
    return {"source": "footwear", "tables": {"skus": skus, "locations": bins, "aisle_nodes": nodes,
                                              "orders": orders, "order_lines": lines},
            "unknown": ["warehouse identity and timezone", "order status", "SKU size identity", "stock snapshot",
                        "package dimensions and weight", "bin dimensions and capacity", "hazard and temperature policy",
                        "aisle edges and access", "certified packing capacity"],
            "counts": {"products": len(products), "locations": len(locations), "orders": len(orders), "order_lines": len(lines)}}


def slaprp_projection(archive: Path, member: str) -> dict:
    """Parse the documented Guo/Silva text template; retain fixed assignment IDs."""
    with zipfile.ZipFile(archive) as z:
        if member not in z.namelist() or not member.endswith(".txt") or not (
                "instances_Guo_2021/" in member or member.startswith("Instances/Small/") or member.startswith("Instances/Regular/")):
            raise ValueError("Select a published Guo or Silva instance")
        lines = [line.strip() for line in z.read(member).decode("utf-8").splitlines() if line.strip()]
    aisles, bays = map(int, lines[0].split())
    sku_count, order_count = int(lines[2]), int(lines[3])
    order_sizes = [int(value) for value in lines[4].split()]
    if len(order_sizes) != order_count:
        raise ValueError("Order count does not match published order lengths")
    order_sets = [list(map(int, row.split())) for row in lines[5:5 + order_count]]
    if any(len(row) != size for row, size in zip(order_sets, order_sizes)):
        raise ValueError("Published order line count differs from order length")
    skus = [_row("skus", sku_id=str(index)) for index in range(1, sku_count + 1)]
    orders = [_row("orders", order_id=str(index)) for index in range(1, order_count + 1)]
    order_lines = [_row("order_lines", order_line_id=f"{order}-{line}", order_id=str(order), sku_id=str(sku))
                   for order, items in enumerate(order_sets, 1) for line, sku in enumerate(items, 1)]
    fixed = []
    for index, line in enumerate(lines[5 + order_count:], 1):
        sku, location = line.split()
        fixed.append(_row("slot_assignments", slot_assignment_id=f"fixed-{index}", sku_id=sku, location_id=location))
    return {"source": member, "tables": {"skus": skus, "orders": orders, "order_lines": order_lines,
                                          "slot_assignments": fixed},
            "unknown": ["warehouse identity", "timestamps", "order quantities and status", "physical inventory",
                        "SKU/package dimensions and weight", "bin capacity", "hazard and temperature policy",
                        "canonical aisle edges and picker access", "certified packing capacity"],
            "counts": {"aisles": aisles, "bays_per_aisle": bays, "skus": sku_count,
                       "orders": order_count, "order_lines": sum(order_sizes), "fixed_assignments": len(fixed)},
            "demand_frequency": dict(Counter(sku for order in order_sets for sku in order))}


def write_partial_projection(projection: dict, folder: Path) -> None:
    """Write canonical headers and known cells, never a valid operational manifest."""
    folder.mkdir(parents=True, exist_ok=True)
    for table, rows in projection["tables"].items():
        with (folder / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[column.name for column in TABLES[table]])
            writer.writeheader()
            writer.writerows(rows)
    (folder / "missing_fields.json").write_text(json.dumps({"source": projection["source"],
        "unknown": projection["unknown"], "operational_importable": False}, indent=2), encoding="utf-8")
