"""Inspect arbitrary tabular uploads and explicitly map them without inventing facts."""
import csv
import io
import re

from .canonical_schema import TABLES, FULL_REQUIRED
from .live_upload import _csv_rows, _excel_tables, TABLE_ALIASES, COLUMN_ALIASES


def normalized(value):
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def sources(files):
    for filename, content in files:
        if filename.lower() == "manifest.json":
            continue
        suffix = filename.rsplit(".", 1)[-1].lower()
        if suffix == "csv":
            yield filename, filename.rsplit(".", 1)[0], _csv_rows(content, filename)
        elif suffix in {"xlsx", "xlsm"}:
            for sheet, rows in _excel_tables(content, filename).items():
                yield f"{filename}::{sheet}", sheet, rows
        else:
            raise ValueError(f"{filename}: supported formats are CSV, XLSX and XLSM.")


def inspect_files(files):
    profiles = []
    for source, name, rows in sources(files):
        columns = list(rows[0]) if rows else []
        table = TABLE_ALIASES.get(normalized(name), normalized(name))
        if table not in TABLES:
            table = ""
        expected = {c.name for c in TABLES.get(table, [])}
        aliases = COLUMN_ALIASES.get(table, {})
        mapping = {c: aliases.get(normalized(c), normalized(c)) for c in columns}
        mapping = {c: target if target in expected else "" for c, target in mapping.items()}
        profiles.append({"source": source, "table": table, "rows": len(rows), "columns": columns,
                         "mapping": mapping, "sample": rows[:3],
                         "blank_cells": sum(not str(v).strip() for r in rows for v in r.values())})
    return {"sources": profiles,
            "schemas": {table: [{"name": c.name, "required": c.required, "type": c.kind} for c in cols] for table, cols in TABLES.items()},
            "required_tables": sorted(FULL_REQUIRED),
            "note": "Inspection accepts unrelated datasets. Placement optimization requires verified physical, inventory, policy and route data. Blank fields remain unknown; column mapping does not convert units or establish safety approvals."}


def mapped_files(files, mappings):
    if any(name.lower() == "manifest.json" for name, _ in files):
        raise ValueError("Manifest packages must retain their original headers and checksums. Use the original package without mapping.")
    available = {source: rows for source, _, rows in sources(files)}
    output = []
    tables = set()
    for spec in mappings:
        source, table, columns = spec.get("source"), spec.get("table"), spec.get("mapping")
        if not table:
            continue
        if not isinstance(source, str) or not isinstance(table, str) or source not in available or table not in TABLES or not isinstance(columns, dict):
            raise ValueError("Unknown source, canonical table or invalid column mapping.")
        if table in tables:
            raise ValueError(f"Duplicate mapped table {table}; combine source exports explicitly first.")
        tables.add(table)
        expected = {c.name for c in TABLES[table]}
        if any(not isinstance(key, str) or not isinstance(target, str) for key, target in columns.items()):
            raise ValueError("Column names and mapping targets must be strings.")
        targets = [target for target in columns.values() if target]
        headers = set(available[source][0]) if available[source] else set()
        if any(key not in headers for key in columns) or any(target not in expected for target in targets) or len(targets) != len(set(targets)):
            raise ValueError(f"{source}: unknown or duplicate mapped columns.")
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=targets)
        writer.writeheader()
        for row in available[source]:
            writer.writerow({target: row[key] for key, target in columns.items() if target})
        output.append((table + ".csv", stream.getvalue().encode("utf-8")))
    if not output:
        raise ValueError("Map at least one source table to process warehouse placements.")
    return output
