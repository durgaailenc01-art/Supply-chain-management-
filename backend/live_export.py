"""Downloadable review sheets generated from a completed live engine response."""
from __future__ import annotations

import csv
import io
from openpyxl import Workbook


PLACEMENT_FIELDS = ("warehouse_id", "snapshot_at", "sku_id", "sku_name", "current_location_id",
                    "proposed_location_id", "decision", "status", "pick_count", "demand_units",
                    "current_round_trip_m", "proposed_round_trip_m", "current_estimated_refill_cycles",
                    "estimated_refill_cycles", "reason", "constraint_violations")
MOVEMENT_FIELDS = ("warehouse_id", "snapshot_at", "sku_id", "source_location_id", "proposed_location_id",
                   "packaging_profile_id", "lot_id", "handling_unit_count", "on_hand_qty", "base_uom",
                   "stock_status", "action")


def export_rows(result: dict) -> tuple[list[dict], list[dict]]:
    placements = []
    for recommendation in result["recommendations"]:
        row = {"warehouse_id": result["warehouse_id"], "snapshot_at": result["snapshot_at"], **recommendation}
        row["constraint_violations"] = "; ".join(row["constraint_violations"])
        placements.append({key: row.get(key) for key in PLACEMENT_FIELDS})
    movements = [{key: {"warehouse_id": result["warehouse_id"], "snapshot_at": result["snapshot_at"], **item}.get(key)
                  for key in MOVEMENT_FIELDS} for item in result["movement_lines"]]
    return placements, movements


def csv_bytes(fields: tuple[str, ...], rows: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def xlsx_bytes(result: dict) -> bytes:
    placements, movements = export_rows(result)
    workbook = Workbook()
    for sheet, fields, rows in ((workbook.active, PLACEMENT_FIELDS, placements),
                                (workbook.create_sheet('Stock movements'), MOVEMENT_FIELDS, movements)):
        sheet.title = 'Placements' if sheet == workbook.active else 'Stock movements'
        sheet.append(list(fields))
        sheet.freeze_panes = 'A2'
        for row in rows:
            sheet.append([row.get(field) for field in fields])
        sheet.auto_filter.ref = f'A1:{sheet.cell(row=max(1, len(rows) + 1), column=len(fields)).coordinate}'
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
