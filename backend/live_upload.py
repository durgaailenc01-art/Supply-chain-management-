"""CSV/XLSX upload preparation for the canonical validator and live engine."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .canonical_import import ImportResult, _Check, _report, _validate_physical, _validate_relations, import_package, _read_table
from .canonical_schema import FULL_REQUIRED, LEGACY_COLUMN_ALIASES, PRIMARY_KEYS, TABLES
from .live_engine import optimize_canonical

LOG = logging.getLogger("slotwise.live")

TABLE_ALIASES = {"product": "skus", "products": "skus", "sales_order": "orders", "sales_order_details": "order_lines", "stock": "inventory_by_location", "inventory": "inventory_by_location", "assignments": "slot_assignments", "bins": "locations"}
COLUMN_ALIASES = {
    "location_access": LEGACY_COLUMN_ALIASES["location_access"],
    "skus": {"product_id": "sku_id", "product_name": "name"},
    "orders": {"sales_order_id": "order_id", "order_date": "ordered_on"},
    "order_lines": {"sales_order_detail_id": "order_line_id", "sales_order_id": "order_id", "product_id": "sku_id", "quantity_ordered": "ordered_qty"},
    "inventory_by_location": {"quantity": "on_hand_qty", "stock_qty": "on_hand_qty", "bin_id": "location_id", "product_id": "sku_id"},
    "locations": {"bin_id": "location_id", "capacity_cm3": "usable_volume_cm3", "max_weight_kg": "max_total_weight_kg", "length_cm": "usable_length_cm", "width_cm": "usable_width_cm", "height_cm": "usable_height_cm"},
    "sku_packaging_profiles": {"product_id": "sku_id", "length_cm": "outer_length_cm", "width_cm": "outer_width_cm", "height_cm": "outer_height_cm", "weight_kg": "gross_weight_kg"},
}


def _csv_rows(data: bytes, filename: str) -> list[dict[str, str]]:
    try:
        text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect, strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("header is missing or duplicated")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("one or more rows have an incorrect number of columns")
        return rows
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise ValueError(f"{filename}: invalid CSV: {exc}") from exc


def _excel_tables(data: bytes, filename: str) -> dict[str, list[dict[str, str]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("Excel support requires the openpyxl backend dependency") from exc
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        output = {}
        for sheet in book:
            values = sheet.iter_rows(values_only=True)
            header = next(values, None)
            if not header or not any(cell is not None for cell in header):
                continue
            names = [str(cell).strip() if cell is not None else "" for cell in header]
            if not all(names) or len(names) != len(set(names)):
                raise ValueError(f"{filename}/{sheet.title}: blank or duplicate headers")
            rows = []
            for row in values:
                if not any(cell is not None for cell in row):
                    continue
                if len(row) > len(names) and any(cell is not None for cell in row[len(names):]):
                    raise ValueError(f"{filename}/{sheet.title}: row exceeds header width")
                rows.append({key: _excel_value(row[i]) if i < len(row) else "" for i, key in enumerate(names)})
            output[sheet.title.casefold()] = rows
        book.close()
        return output
    except (OSError, ValueError) as exc:
        raise ValueError(f"{filename}: invalid Excel workbook: {exc}") from exc


def _excel_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timezone-naive Excel timestamp; convert it to explicit UTC text")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _canonicalize(table: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    expected = {col.name for col in TABLES[table]}
    kinds = {col.name: col.kind for col in TABLES[table]}
    aliases = COLUMN_ALIASES.get(table, {})
    result = []
    for i, raw in enumerate(rows, 2):
        mapped = {}
        mapped_sources = {}
        for key, value in raw.items():
            source = re.sub(r"[^a-z0-9]+", "_", key.strip().casefold()).strip("_")
            target = aliases.get(source, source)
            if target in expected:
                value = str(value) if value is not None else ""
                if kinds[target] in {"bool", "enum"}:
                    value = value.strip().lower()
                if kinds[target] == "int" and re.fullmatch(r"-?(0|[1-9][0-9]*)\.0+", value):
                    value = value.split(".")[0]
                if target in mapped:
                    legacy_names = set(aliases)
                    if source in legacy_names or mapped_sources[target] in legacy_names:
                        raise ValueError(f"{table} row {i}: duplicate columns for {target}")
                    if mapped[target] != value:
                        raise ValueError(f"{table} row {i}: conflicting columns for {target}")
                mapped[target] = value
                mapped_sources[target] = source
        missing = [col.name for col in TABLES[table] if col.required and not mapped.get(col.name)]
        if missing:
            raise ValueError(f"{table} row {i}: missing required fields {', '.join(missing)}. Supply a canonical export or map these source fields explicitly.")
        result.append({col.name: mapped.get(col.name, "") for col in TABLES[table]})
    return result


def run_upload(files: list[tuple[str, bytes]], warehouse_name: str, snapshot_at: str) -> dict:
    try:
        snapshot = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Enter an exact snapshot date and time with a UTC offset.") from exc
    if snapshot.tzinfo is None:
        raise ValueError("Snapshot time needs a timezone offset.")
    snapshot = snapshot.astimezone(timezone.utc)
    if not warehouse_name.strip():
        raise ValueError("Select or enter a warehouse name.")
    if not files:
        raise ValueError("Select at least one CSV or XLSX file.")
    if len(files) > 32 or any(len(content) > 25_000_000 for _, content in files):
        raise ValueError("Upload at most 32 files, each 25 MB or smaller.")
    tables = {}
    raw_csv = {}
    manifest_bytes = None
    for filename, content in files:
        base = Path(filename).name
        if base != filename or not re.fullmatch(r"[A-Za-z0-9_. -]+", base):
            raise ValueError(f"Unsafe file name: {filename}")
        stem, suffix = Path(base).stem.casefold(), Path(base).suffix.casefold()
        if base.casefold() == "manifest.json":
            if manifest_bytes is not None:
                raise ValueError("Only one manifest.json can be uploaded")
            manifest_bytes = content
            continue
        if suffix == ".csv":
            raw_csv[base] = content
            parsed = {stem: _csv_rows(content, base)}
        elif suffix in {".xlsx", ".xlsm"}:
            parsed = _excel_tables(content, base)
        else:
            raise ValueError(f"Unsupported file {base}; use CSV, XLSX, XLSM or manifest.json")
        for source_name, rows in parsed.items():
            table = TABLE_ALIASES.get(source_name, source_name)
            if table not in TABLES:
                LOG.warning("Ignoring unrecognized upload table %s from %s", source_name, base)
                continue
            if table in tables:
                raise ValueError(f"Duplicate table {table}; upload one source for each canonical table")
            tables[table] = rows
    if manifest_bytes is not None:
        if snapshot > datetime.now(timezone.utc):
            raise ValueError("Snapshot time cannot be in the future for a manifest-backed operational snapshot.")
        try:
            preview = json.loads(manifest_bytes)
            manifest_snapshot = datetime.fromisoformat(str(preview["snapshot_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, KeyError, TypeError, AttributeError):
            preview = None  # The canonical importer reports the precise manifest error.
        if preview is not None:
            if manifest_snapshot != snapshot:
                raise ValueError(f"Entered snapshot_at={snapshot.isoformat()} differs from manifest.snapshot_at={manifest_snapshot.isoformat()}. Use the exact manifest snapshot time.")
            available = [(row.get("warehouse_id", ""), row.get("name", "")) for row in tables.get("warehouses", [])]
            if available and not any(warehouse_name.strip().casefold() in {warehouse_id.casefold(), name.casefold()} for warehouse_id, name in available):
                choices = ", ".join(f"{name} ({warehouse_id})" for warehouse_id, name in available[:20])
                raise ValueError(f"Warehouse {warehouse_name!r} is not in the uploaded package. Available: {choices}")
        if len(raw_csv) != len(files) - 1:
            raise ValueError("A manifest package must contain CSV files only; Excel sheets cannot retain its byte checksums")
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            folder = Path(temp)
            (folder / "manifest.json").write_bytes(manifest_bytes)
            for name, content in raw_csv.items():
                (folder / name).write_bytes(content)
            imported = import_package(folder)
        if not imported.valid:
            issues = "; ".join(f"{e['table']} row {e['row']}: {e['detail']}" for e in imported.report["errors"][:8])
            raise ValueError(f"Canonical validation failed: {issues}")
        if imported.manifest["snapshot_at"] != snapshot:
            raise ValueError("Entered snapshot time must exactly match manifest.snapshot_at")
    else:
        missing = sorted(FULL_REQUIRED - set(tables))
        if missing:
            raise ValueError("Missing physical warehouse tables: " + ", ".join(missing) + ". Sales data alone cannot support slot recommendations.")
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            folder = Path(temp)
            for table, rows in tables.items():
                normalized = _canonicalize(table, rows)
                with (folder / f"{table}.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=[col.name for col in TABLES[table]])
                    writer.writeheader()
                    writer.writerows(normalized)
            check = _Check()
            if snapshot > datetime.now(timezone.utc):
                check.warn("future_snapshot_preview", "Snapshot timestamp is in the future; this manifestless result is a scenario preview and is not a current operational plan.")
            parsed_tables = {name: _read_table(folder / f"{name}.csv", name, check) if name in tables else [] for name in TABLES}
            warehouse_ids = [r["warehouse_id"] for r in parsed_tables["warehouses"]]
            manifest = {"schema_version": "1.0", "dataset_id": f"upload-{snapshot.strftime('%Y%m%dT%H%M%SZ')}",
                        "import_kind": "full_snapshot", "snapshot_at": snapshot, "warehouse_ids": warehouse_ids,
                        "files": {f"{name}.csv": {} for name in tables}, "_snapshot_label": "entered snapshot_at",
                        "_allow_post_snapshot_demand": True}
            if "sku_uom_conversions" in tables:
                manifest["files"]["sku_uom_conversions.csv"]["source_extracts"] = [
                    {"source_system": source} for source in {row["source_system"] for row in parsed_tables["sku_uom_conversions"]}
                ]
            if not check.errors:
                _validate_relations(manifest, parsed_tables, check)
            eligible = _validate_physical(manifest, parsed_tables, check) if not check.errors else {}
            check.warn("snapshot_unverified", "No manifest was supplied; cross-file snapshot consistency and lineage are unverified")
            imported = ImportResult(manifest, parsed_tables, _report(manifest, parsed_tables, check, eligible))
        if not imported.valid:
            issues = "; ".join(f"{e['table']} row {e['row']}: {e['detail']}" for e in imported.report["errors"][:8])
            raise ValueError(f"Uploaded data failed validation: {issues}")
    match = [w for w in imported.tables["warehouses"] if w["name"].casefold() == warehouse_name.strip().casefold() or w["warehouse_id"].casefold() == warehouse_name.strip().casefold()]
    if len(match) != 1:
        names = ", ".join(f"{w['name']} ({w['warehouse_id']})" for w in imported.tables["warehouses"])
        raise ValueError(f"Warehouse {warehouse_name!r} was not found uniquely. Available: {names}")
    LOG.info("Upload received: %s | selected warehouse %s | snapshot %s", [name for name, _ in files], match[0]["warehouse_id"], snapshot)
    if imported.report["errors"]:
        LOG.error("Validation errors: %s", imported.report["errors"])
    if imported.report["incidents"]:
        LOG.warning("Validation incidents: %s", imported.report["incidents"])
    for warning in imported.report["warnings"]:
        if warning["code"] == "unsafe_pack_capacity":
            LOG.warning("Excluded unsafe capacity certificate: %s", warning["detail"])
    return optimize_canonical(imported, match[0]["warehouse_id"], match[0]["name"])
