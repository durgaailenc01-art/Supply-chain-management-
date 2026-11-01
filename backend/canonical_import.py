"""Read and validate a Slotwise canonical CSV package without invoking the optimizer.

The legacy synthetic dataset has a different schema and remains on backend.validate.
This importer never invents missing physical measurements or writes into data/starter.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .canonical_schema import DEMAND_REQUIRED, ENUMS, FULL_REQUIRED, LEGACY_COLUMN_ALIASES, PRIMARY_KEYS, TABLES


def _zone(name: str):
    # Minimal Windows Python installs may omit the system IANA database.
    # tzdata is an application dependency for all non-UTC warehouse zones.
    return timezone.utc if name in {"UTC", "Etc/UTC"} else ZoneInfo(name)


@dataclass
class ImportResult:
    manifest: dict
    tables: dict[str, list[dict]]
    report: dict

    @property
    def valid(self) -> bool:
        return self.report["valid"]


@dataclass
class _Check:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)
    blocked: dict[str, dict[str, set[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))

    def error(self, code: str, table: str, row: int | None, detail: str, warehouse: str | None = None) -> None:
        self.errors.append({"code": code, "table": table, "row": row, "warehouse_id": warehouse, "detail": detail})

    def warn(self, code: str, detail: str) -> None:
        self.warnings.append({"code": code, "detail": detail})

    def incident(self, code: str, warehouse: str, location: str, detail: str) -> None:
        self.incidents.append({"code": code, "warehouse_id": warehouse, "location_id": location, "detail": detail})
        self.blocked[warehouse][location].add(code)


def _utc(value: str) -> datetime:
    if not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("timestamp must use UTC Z or +00:00")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("timestamp must be UTC")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse(value: str, kind: str, allowed: set[str] | None) -> object:
    if kind in {"id", "text", "enum"}:
        if kind == "id" and value != value.strip():
            raise ValueError("ID has surrounding whitespace")
        if kind == "enum" and allowed is not None and value not in allowed:
            raise ValueError(f"invalid enum {value!r}; expected {sorted(allowed)}")
        return value
    if kind == "bool":
        if value not in {"true", "false"}:
            raise ValueError("boolean must be true or false")
        return value == "true"
    if kind == "int":
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
            raise ValueError("invalid integer")
        return int(value)
    if kind == "decimal":
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value):
            raise ValueError("invalid base-10 decimal")
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("nonfinite decimal")
        return number
    if kind == "timestamp":
        return _utc(value)
    if kind == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must be YYYY-MM-DD")
        return date.fromisoformat(value)
    raise ValueError(f"unhandled column type {kind}")


def _read_table(path: Path, name: str, check: _Check) -> list[dict]:
    schema = TABLES[name]
    expected = {column.name for column in schema}
    rows = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            header = reader.fieldnames or []
            aliases = LEGACY_COLUMN_ALIASES.get(name, {})
            mapped_header = [aliases.get(column, column) for column in header]
            if len(mapped_header) != len(set(mapped_header)) or set(mapped_header) != expected:
                check.error("csv_headers", name, 1, f"missing={sorted(expected - set(mapped_header))}; extra={sorted(set(mapped_header) - expected)}; duplicate={len(mapped_header) != len(set(mapped_header))}")
                return rows
            source_header = dict(zip(mapped_header, header))
            if any(column in aliases for column in header):
                check.warn("legacy_column_alias", f"{name}: access_offset_m is deprecated; use walk_offset_m")
            for line_number, raw in enumerate(reader, 2):
                if None in raw or any(value is None for value in raw.values()):
                    check.error("csv_width", name, line_number, "row has a different number of fields than header")
                    continue
                item = {"_row": line_number}
                bad = False
                for column in schema:
                    value = raw[source_header[column.name]]
                    if value == "":
                        if column.required:
                            check.error("required", name, line_number, f"{column.name} is blank")
                            bad = True
                        item[column.name] = None
                        continue
                    try:
                        item[column.name] = _parse(value, column.kind, ENUMS.get((name, column.name)))
                    except (ValueError, InvalidOperation, OverflowError) as exc:
                        check.error("field_type", name, line_number, f"{column.name}: {exc}")
                        item[column.name] = None
                        bad = True
                if not bad:
                    rows.append(item)
    except (OSError, UnicodeError, csv.Error) as exc:
        check.error("csv_read", name, None, str(exc))
    return rows


def _manifest(path: Path, check: _Check) -> dict:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        check.error("manifest_read", "manifest", None, str(exc))
        return {}
    if not isinstance(manifest, dict):
        check.error("manifest_type", "manifest", None, "manifest must be a JSON object")
        return {}
    for key in ("schema_version", "dataset_id", "tenant_id", "source_system", "mapping_version",
                "exported_at", "snapshot_at", "consistency_method", "warehouse_ids", "files",
                "source_timezones", "import_kind"):
        if manifest.get(key) in (None, "", [], {}):
            check.error("manifest_required", "manifest", None, f"{key} missing or blank")
    if manifest.get("schema_version") != "1.0":
        check.error("schema_version", "manifest", None, "supported schema_version is 1.0")
    mode = manifest.get("import_kind")
    if not isinstance(mode, str) or mode not in {"full_snapshot", "demand_only"}:
        check.error("import_kind", "manifest", None, "expected full_snapshot or demand_only")
    if mode == "full_snapshot" and not manifest.get("storage_policy_version"):
        check.error("storage_policy_version", "manifest", None, "full_snapshot needs storage_policy_version")
    if mode == "demand_only" and manifest.get("storage_policy_version"):
        check.error("storage_policy_version", "manifest", None, "demand_only must not claim a physical storage policy")
    method = manifest.get("consistency_method")
    if not isinstance(method, str) or method not in ({"transactional", "replayed", "verified_quiescent"} if mode == "full_snapshot" else {"history_extract"}):
        check.error("consistency_method", "manifest", None, "method incompatible with import_kind")
    proof = {"transactional": "consistency_token", "replayed": "replay_proof_id",
             "verified_quiescent": "quiescence_ref", "history_extract": "history_extract_ref"}.get(method) if isinstance(method, str) else None
    if proof and not manifest.get(proof):
        check.error("consistency_evidence", "manifest", None, f"{proof} is required")
    for key in {"consistency_token", "replay_proof_id", "quiescence_ref", "history_extract_ref"} - {proof}:
        if manifest.get(key):
            check.error("consistency_evidence", "manifest", None, f"{key} does not apply to {method}")
    for key in ("snapshot_at", "exported_at", "history_from", "history_to"):
        if manifest.get(key):
            try:
                manifest[key] = _utc(manifest[key])
            except (TypeError, ValueError) as exc:
                check.error("manifest_timestamp", "manifest", None, f"{key}: {exc}")
                manifest[key] = None
    snapshot, exported = manifest.get("snapshot_at"), manifest.get("exported_at")
    if isinstance(snapshot, datetime) and isinstance(exported, datetime):
        if snapshot > exported:
            check.error("manifest_time_order", "manifest", None, "snapshot_at exceeds exported_at")
        if exported > datetime.now(timezone.utc):
            check.error("manifest_time_order", "manifest", None, "exported_at is in the future")
    history_keys = (manifest.get("history_from"), manifest.get("history_to"))
    if any(history_keys) and (not all(history_keys) or history_keys[0] >= history_keys[1] or
                              isinstance(snapshot, datetime) and history_keys[1] > snapshot):
        check.error("history_window", "manifest", None, "invalid history_from/history_to window")
    ids = manifest.get("warehouse_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x or x != x.strip() for x in ids) or len(ids) != len(set(ids)):
        check.error("warehouse_ids", "manifest", None, "warehouse_ids must be unique nonempty IDs")
    zones = manifest.get("source_timezones")
    if not isinstance(zones, dict) or not zones:
        check.error("source_timezones", "manifest", None, "source_timezones must map source IDs to IANA names")
    else:
        for source, zone in zones.items():
            try:
                _zone(zone)
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                check.error("source_timezone", "manifest", None, f"invalid timezone for {source}")
    return manifest


def _load(path: Path, check: _Check) -> tuple[dict, dict[str, list[dict]]]:
    manifest = _manifest(path, check)
    meta = manifest.get("files")
    if not isinstance(meta, dict):
        check.error("files", "manifest", None, "files must be a filename-keyed object")
        meta = {}
    names = set()
    for filename, info in meta.items():
        if not isinstance(filename, str) or not re.fullmatch(r"[a-z_]+\.csv", filename) or filename[:-4] not in TABLES:
            check.error("file_name", "manifest", None, f"unsupported or unsafe file name {filename!r}")
            continue
        names.add(filename[:-4])
        if not isinstance(info, dict) or info.get("filename") != filename:
            check.error("file_metadata", "manifest", None, f"{filename}: filename mismatch")
            continue
        if not isinstance(info.get("rows"), int) or isinstance(info.get("rows"), bool) or info["rows"] < 0:
            check.error("file_metadata", "manifest", None, f"{filename}: invalid row count")
        if not isinstance(info.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", info["sha256"]):
            check.error("file_metadata", "manifest", None, f"{filename}: invalid sha256")
        extracts = info.get("source_extracts")
        declared_rows = info.get("rows") if isinstance(info.get("rows"), int) and not isinstance(info.get("rows"), bool) else None
        if not isinstance(extracts, list) or (declared_rows is not None and declared_rows > 0 and not extracts) or (not extracts and not info.get("empty_scope_ref")):
            check.error("source_extracts", "manifest", None, f"{filename}: missing source extract or empty-scope proof")
            extracts = []
        for extract in extracts:
            if not isinstance(extract, dict) or not all(isinstance(extract.get(k), str) and extract[k] for k in ("source_system", "source_extract_id", "captured_at")):
                check.error("source_extracts", "manifest", None, f"{filename}: invalid source extract")
                continue
            if extract["source_system"] not in manifest.get("source_timezones", {}):
                check.error("source_extracts", "manifest", None, f"{filename}: unregistered source_system")
            try:
                captured = _utc(extract["captured_at"])
                if isinstance(manifest.get("exported_at"), datetime) and captured > manifest["exported_at"]:
                    check.error("source_extracts", "manifest", None, f"{filename}: captured_at after exported_at")
            except ValueError as exc:
                check.error("source_extracts", "manifest", None, f"{filename}: {exc}")
    required = FULL_REQUIRED if manifest.get("import_kind") == "full_snapshot" else DEMAND_REQUIRED
    for name in sorted(required - names):
        check.error("missing_file", "manifest", None, f"{name}.csv is required")
    if manifest.get("import_kind") == "demand_only" and names - (DEMAND_REQUIRED | {"sku_uom_conversions"}):
        check.error("mode_file", "manifest", None, f"demand_only has unsupported files: {sorted(names - (DEMAND_REQUIRED | {'sku_uom_conversions'}))}")
    if ("orders" in names) != ("order_lines" in names):
        check.error("history_pair", "manifest", None, "orders.csv and order_lines.csv must be supplied together")
    if names & {"orders", "pick_events", "replenishment_events"} and not all(manifest.get(k) for k in ("history_from", "history_to")):
        check.error("history_window", "manifest", None, "history_from/history_to required with history")
    actual = {p.name for p in path.glob("*.csv")}
    if actual != {f"{name}.csv" for name in names}:
        check.error("file_set", "manifest", None, f"unlisted={sorted(actual - {f'{n}.csv' for n in names})}; absent={sorted({f'{n}.csv' for n in names} - actual)}")
    tables = {name: [] for name in TABLES}
    for name in sorted(names):
        file = path / f"{name}.csv"
        if not file.is_file():
            continue
        try:
            raw_bytes = file.read_bytes()
        except OSError as exc:
            check.error("csv_read", name, None, str(exc))
            continue
        info = meta[f"{name}.csv"]
        if isinstance(info, dict) and hashlib.sha256(raw_bytes).hexdigest() != info.get("sha256"):
            check.error("checksum", name, None, "SHA-256 does not match manifest")
        rows = _read_table(file, name, check)
        tables[name] = rows
        if isinstance(info, dict) and len(rows) != info.get("rows"):
            check.error("row_count", name, None, f"manifest says {info.get('rows')}, parsed {len(rows)}")
    return manifest, tables


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    return {row[key]: row for row in rows if row.get(key) is not None}


def _fk(check: _Check, table: str, row: dict, field: str, target: dict, *, optional: bool = False) -> dict | None:
    value = row.get(field)
    if value is None and optional:
        return None
    item = target.get(value)
    if item is None:
        check.error("foreign_key", table, row["_row"], f"{field}={value!r} does not resolve")
    return item


def _same_warehouse(check: _Check, table: str, row: dict, target: dict | None) -> None:
    if target is not None and row.get("warehouse_id") != target.get("warehouse_id"):
        check.error("warehouse_join", table, row["_row"], "cross-warehouse reference", row.get("warehouse_id"))


def _require(check: _Check, table: str, row: dict, condition: bool, detail: str) -> None:
    if not condition:
        check.error("conditional", table, row["_row"], detail, row.get("warehouse_id"))


def _range(check: _Check, table: str, row: dict, field: str, *, minimum: int | Decimal = 0, strict: bool = False) -> None:
    value = row.get(field)
    if value is not None and (value <= minimum if strict else value < minimum):
        check.error("range", table, row["_row"], f"{field} must be {'>' if strict else '≥'} {minimum}", row.get("warehouse_id"))


def _time_pair(check: _Check, table: str, row: dict, start: str, end: str, *, strict: bool = False) -> None:
    a, b = row.get(start), row.get(end)
    if a is not None and b is not None and (a >= b if strict else a > b):
        check.error("time_order", table, row["_row"], f"{start} must be {'<' if strict else '≤'} {end}", row.get("warehouse_id"))


def _effective(row: dict, when: datetime) -> bool:
    start, end = row.get("effective_from"), row.get("effective_to")
    return (start is None or start <= when) and (end is None or when < end)


def _overlap(a: dict, b: dict) -> bool:
    low = datetime.min.replace(tzinfo=timezone.utc)
    high = datetime.max.replace(tzinfo=timezone.utc)
    return (a.get("effective_from") or low) < (b.get("effective_to") or high) and (b.get("effective_from") or low) < (a.get("effective_to") or high)


def _profile_for(tables: dict, sku_id: str, warehouse_id: str, when: datetime) -> dict | None:
    rows = [p for p in tables["sku_profiles"] if p["sku_id"] == sku_id and
            p["warehouse_id"] in (None, warehouse_id) and _effective(p, when)]
    local = [p for p in rows if p["warehouse_id"] == warehouse_id]
    chosen = local or [p for p in rows if p["warehouse_id"] is None]
    return chosen[0] if len(chosen) == 1 else None


def _validate_relations(manifest: dict, tables: dict[str, list[dict]], check: _Check) -> None:
    indexes = {name: _index(rows, PRIMARY_KEYS[name]) for name, rows in tables.items()}
    for name, rows in tables.items():
        key = PRIMARY_KEYS[name]
        seen = set()
        for row in rows:
            if row[key] in seen:
                check.error("primary_key", name, row["_row"], f"duplicate {key}={row[key]!r}")
            seen.add(row[key])
    wh, zone, loc, hist = (indexes[name] for name in ("warehouses", "zones", "locations", "historical_location_refs"))
    sku, pkg, lot = (indexes[name] for name in ("skus", "sku_packaging_profiles", "sku_lots"))
    node, orders, lines = (indexes[name] for name in ("aisle_nodes", "orders", "order_lines"))
    plan = indexes["shared_bin_plans"]
    snapshot = manifest.get("snapshot_at")
    snapshot_label = manifest.get("_snapshot_label", "manifest snapshot_at")
    if isinstance(manifest.get("warehouse_ids"), list) and all(isinstance(x, str) for x in manifest["warehouse_ids"]) and set(manifest["warehouse_ids"]) != set(wh):
        check.error("warehouse_scope", "manifest", None, "warehouse_ids must equal warehouses.csv IDs")
    if not isinstance(snapshot, datetime):
        return
    for row in tables["warehouses"]:
        if row["timezone"]:
            try:
                _zone(row["timezone"])
            except (ValueError, ZoneInfoNotFoundError):
                check.error("warehouse_timezone", "warehouses", row["_row"], "invalid IANA timezone", row["warehouse_id"])
        if row["default_pick_node_id"]:
            target = _fk(check, "warehouses", row, "default_pick_node_id", node)
            if target and (target["warehouse_id"] != row["warehouse_id"] or target["node_type"] not in {"depot", "packing"} or target["status"] != "active"):
                check.error("default_node", "warehouses", row["_row"], "default pick node must be active depot/packing in same warehouse", row["warehouse_id"])
    for row in tables["zones"]:
        _fk(check, "zones", row, "warehouse_id", wh)
    for row in tables["locations"]:
        _fk(check, "locations", row, "warehouse_id", wh)
        _same_warehouse(check, "locations", row, _fk(check, "locations", row, "zone_id", zone))
        dims = [row[k] for k in ("usable_length_cm", "usable_width_cm", "usable_height_cm")]
        _require(check, "locations", row, all(x is None for x in dims) or all(x is not None for x in dims), "clear dimensions must be all present or all blank")
        for key in ("usable_length_cm", "usable_width_cm", "usable_height_cm", "usable_volume_cm3", "max_total_weight_kg"):
            _range(check, "locations", row, key, strict=True)
        for key in ("max_distinct_skus", "max_lots_per_sku", "pick_height_cm"):
            _range(check, "locations", row, key, minimum=1 if key == "max_lots_per_sku" else 0)
        if all(x is not None for x in dims) and row["usable_volume_cm3"] is not None and row["usable_volume_cm3"] > dims[0] * dims[1] * dims[2]:
            check.error("volume_bound", "locations", row["_row"], "usable volume exceeds clear rectangular volume", row["warehouse_id"])
        pair = (row["operating_temp_min_c"], row["operating_temp_max_c"])
        _require(check, "locations", row, (pair[0] is None) == (pair[1] is None), "temperature bounds must be both present or both blank")
        _time_pair(check, "locations", row, "operating_temp_min_c", "operating_temp_max_c")
        if row["allow_mixed_lots"] is False and row["max_lots_per_sku"] not in (None, 1):
            check.error("lot_limit", "locations", row["_row"], "max_lots_per_sku must be 1 when mixing is false", row["warehouse_id"])
    for row in tables["historical_location_refs"]:
        _fk(check, "historical_location_refs", row, "warehouse_id", wh)
        if row["location_id"] in loc:
            check.error("historical_location", "historical_location_refs", row["_row"], "historical ID also appears in current locations")
        if row["retired_at"] and row["retired_at"] > snapshot:
            check.error("historical_location", "historical_location_refs", row["_row"], "retired_at after snapshot")
    for row in tables["skus"]:
        if row["quantity_precision"] is not None and not 0 <= row["quantity_precision"] <= 6:
            check.error("quantity_precision", "skus", row["_row"], "must be 0 through 6")
    for name in ("sku_profiles", "sku_uom_conversions", "sku_packaging_profiles"):
        for row in tables[name]:
            _fk(check, name, row, "sku_id", sku)
            if row["warehouse_id"]:
                _fk(check, name, row, "warehouse_id", wh)
            _time_pair(check, name, row, "effective_from", "effective_to", strict=True)
            if row["effective_to"] and not row["effective_from"]:
                check.error("effective_date", name, row["_row"], "effective_to requires effective_from")
    for row in tables["sku_profiles"]:
        dims = [row[k] for k in ("unit_length_cm", "unit_width_cm", "unit_height_cm")]
        _require(check, "sku_profiles", row, all(x is None for x in dims) or all(x is not None for x in dims), "bare-unit dimensions must be all present or all blank")
        for key in ("unit_length_cm", "unit_width_cm", "unit_height_cm", "unit_weight_kg"):
            _range(check, "sku_profiles", row, key, strict=True)
        _time_pair(check, "sku_profiles", row, "storage_temp_min_c", "storage_temp_max_c")
        if row["stackable"] is True:
            _require(check, "sku_profiles", row, row["max_stack_units"] is not None and row["max_stack_units"] >= 1, "stackable bare unit needs max_stack_units ≥1")
        elif row["max_stack_units"] is not None:
            check.error("stack_rule", "sku_profiles", row["_row"], "max_stack_units only with stackable=true")
    for row in tables["sku_uom_conversions"]:
        _range(check, "sku_uom_conversions", row, "base_units_per_source_unit", strict=True)
        _require(check, "sku_uom_conversions", row, row["effective_from"] is not None, "conversion needs effective_from")
        product = sku.get(row["sku_id"])
        if product and row["source_uom"] == product["base_uom"] and row["base_units_per_source_unit"] != 1:
            check.error("uom_identity", "sku_uom_conversions", row["_row"], "base UOM conversion must equal 1")
        all_sources = {x.get("source_system") for item in manifest.get("files", {}).values() if isinstance(item, dict) for x in item.get("source_extracts", []) if isinstance(x, dict)}
        if row["source_system"] not in all_sources:
            check.error("source_system", "sku_uom_conversions", row["_row"], "source system absent from manifest extracts")
    for row in tables["sku_packaging_profiles"]:
        for key in ("base_units_per_pack", "outer_length_cm", "outer_width_cm", "outer_height_cm", "gross_weight_kg"):
            _range(check, "sku_packaging_profiles", row, key, strict=True)
        _require(check, "sku_packaging_profiles", row, row["effective_from"] is not None, "package needs effective_from")
        if row["packaging_code"] == "base" and row["base_units_per_pack"] != 1:
            check.error("base_package", "sku_packaging_profiles", row["_row"], "base package must contain one base unit")
        if row["stackable"] is True:
            _require(check, "sku_packaging_profiles", row, row["max_stack_layers"] is not None and row["max_stack_layers"] >= 1 and row["max_supported_top_load_kg"] is not None and row["max_supported_top_load_kg"] >= 0, "stackable pack needs layer and top-load limits")
        elif row["max_stack_layers"] is not None or row["max_supported_top_load_kg"] is not None:
            check.error("stack_rule", "sku_packaging_profiles", row["_row"], "stack fields only with stackable=true")
    for name, keyfunc in (
        ("sku_profiles", lambda r: (r["sku_id"], r["warehouse_id"])),
        ("sku_uom_conversions", lambda r: (r["sku_id"], r["warehouse_id"], r["source_system"], r["source_uom"])),
        ("sku_packaging_profiles", lambda r: (r["sku_id"], r["warehouse_id"], r["packaging_code"])),
        ("co_storage_rules", lambda r: (r["warehouse_id"], *sorted((r["group_a"], r["group_b"])))),
        ("hazard_segregation_rules", lambda r: (r["warehouse_id"], *sorted((r["hazard_group_a"], r["hazard_group_b"])))),
    ):
        groups = defaultdict(list)
        for row in tables[name]:
            groups[keyfunc(row)].append(row)
        for values in groups.values():
            for i, first in enumerate(values):
                for second in values[i + 1:]:
                    if name == "sku_profiles" and (first["effective_from"] is None or second["effective_from"] is None):
                        overlaps = _effective(first, snapshot) and _effective(second, snapshot)
                    else:
                        overlaps = _overlap(first, second)
                    if overlaps:
                        check.error("effective_overlap", name, second["_row"], f"overlapping versions with row {first['_row']}", second.get("warehouse_id"))
    for row in tables["sku_lots"]:
        product = _fk(check, "sku_lots", row, "sku_id", sku)
        if product and product["lot_tracking"] == "none":
            check.error("lot_tracking", "sku_lots", row["_row"], "untracked SKU has lot master")
        if row["received_at"] and row["received_at"] > snapshot:
            check.error("lot_time", "sku_lots", row["_row"], "received_at after snapshot")
    lot_pairs = set()
    for row in tables["sku_lots"]:
        pair = (row["sku_id"], row["lot_id"])
        if pair in lot_pairs:
            check.error("lot_key", "sku_lots", row["_row"], "duplicate (sku_id, lot_id)")
        lot_pairs.add(pair)
    post_snapshot_orders = []
    for row in tables["orders"]:
        parent = _fk(check, "orders", row, "warehouse_id", wh)
        _require(check, "orders", row, row["ordered_at"] is not None or row["ordered_on"] is not None, "ordered_at or ordered_on required")
        if row["ordered_at"] and row["ordered_at"] > snapshot:
            if manifest.get("_allow_post_snapshot_demand"):
                post_snapshot_orders.append(row)
            else:
                check.error("order_time", "orders", row["_row"], f"order at {_timestamp_text(row['ordered_at'])} is after snapshot_at={_timestamp_text(snapshot)}", row["warehouse_id"])
        if parent and row["ordered_on"]:
            try:
                if row["ordered_on"] > snapshot.astimezone(_zone(parent["timezone"])).date():
                    if manifest.get("_allow_post_snapshot_demand"):
                        post_snapshot_orders.append(row)
                    else:
                        check.error("order_time", "orders", row["_row"], f"order date {row['ordered_on'].isoformat()} is after warehouse snapshot date", row["warehouse_id"])
            except (ValueError, ZoneInfoNotFoundError):
                pass
        if parent and row["ordered_at"] and row["ordered_on"]:
            try:
                local_day = row["ordered_at"].astimezone(_zone(parent["timezone"])).date()
                if local_day != row["ordered_on"]:
                    check.error("order_date", "orders", row["_row"], "ordered_on disagrees with local ordered_at date", row["warehouse_id"])
            except (ValueError, ZoneInfoNotFoundError):
                pass
        if row["due_at"]:
            if row["ordered_at"] and row["due_at"] < row["ordered_at"]:
                check.error("order_due", "orders", row["_row"], "due before order", row["warehouse_id"])
            elif row["ordered_on"] and parent:
                try:
                    if row["due_at"].astimezone(_zone(parent["timezone"])).date() < row["ordered_on"]:
                        check.error("order_due", "orders", row["_row"], "due date before order date", row["warehouse_id"])
                except (ValueError, ZoneInfoNotFoundError):
                    pass
    if post_snapshot_orders:
        unique_rows = sorted({row["_row"] for row in post_snapshot_orders})
        check.warn("post_snapshot_demand",
                   f"{len(unique_rows)} order row(s), including rows {', '.join(map(str, unique_rows[:10]))}, occur after the physical snapshot. Their fulfilled lines are used as a post-snapshot demand window for scenario analysis only; this does not verify temporal alignment or constitute a forward forecast.")
    line_numbers = set()
    for row in tables["order_lines"]:
        _fk(check, "order_lines", row, "order_id", orders)
        _fk(check, "order_lines", row, "sku_id", sku)
        _range(check, "order_lines", row, "ordered_qty", strict=True)
        _range(check, "order_lines", row, "fulfilled_qty")
        if row["fulfilled_qty"] is not None and row["ordered_qty"] is not None and row["fulfilled_qty"] > row["ordered_qty"]:
            check.warn("overfulfilled", f"order_lines row {row['_row']} exceeds ordered quantity; exclude from ordinary calibration")
        if row["line_number"] is not None:
            _range(check, "order_lines", row, "line_number", minimum=1)
            pair = (row["order_id"], row["line_number"])
            if pair in line_numbers:
                check.error("line_number", "order_lines", row["_row"], "duplicate line number within order")
            line_numbers.add(pair)

    lot_by_pair = {(r["sku_id"], r["lot_id"]): r for r in tables["sku_lots"]}
    inventory_keys, serial_keys = set(), set()
    for row in tables["inventory_by_location"]:
        warehouse = row["warehouse_id"]
        _fk(check, "inventory_by_location", row, "warehouse_id", wh)
        _same_warehouse(check, "inventory_by_location", row, _fk(check, "inventory_by_location", row, "location_id", loc))
        product = _fk(check, "inventory_by_location", row, "sku_id", sku)
        physical = _fk(check, "inventory_by_location", row, "packaging_profile_id", pkg)
        if physical and (physical["sku_id"] != row["sku_id"] or physical["warehouse_id"] not in (None, warehouse) or not _effective(physical, snapshot)):
            check.error("package_scope", "inventory_by_location", row["_row"], "package SKU, warehouse scope or effective period invalid", warehouse)
        if _profile_for(tables, row["sku_id"], warehouse, snapshot) is None:
            check.error("profile_coverage", "inventory_by_location", row["_row"], "no unique effective SKU profile", warehouse)
        if row["snapshot_at"] != snapshot:
            check.error("snapshot_time", "inventory_by_location", row["_row"],
                        f"row snapshot_at={_timestamp_text(row['snapshot_at'])} differs from {snapshot_label}={_timestamp_text(snapshot)}", warehouse)
        for key in ("handling_unit_count", "on_hand_qty"):
            _range(check, "inventory_by_location", row, key, minimum=1 if key == "handling_unit_count" else 0, strict=key == "on_hand_qty")
        for key in ("reserved_qty", "hold_qty"):
            _range(check, "inventory_by_location", row, key)
        if all(row[k] is not None for k in ("reserved_qty", "hold_qty", "on_hand_qty")) and row["reserved_qty"] + row["hold_qty"] > row["on_hand_qty"]:
            check.error("availability", "inventory_by_location", row["_row"], "reserved + hold exceeds on hand", warehouse)
        if physical and all(row[k] is not None for k in ("handling_unit_count", "on_hand_qty")):
            capacity = Decimal(row["handling_unit_count"]) * physical["base_units_per_pack"]
            qty = row["on_hand_qty"]
            if row["pack_state"] == "sealed" and qty != capacity:
                check.error("pack_quantity", "inventory_by_location", row["_row"], "sealed base quantity differs from package count × units/pack", warehouse)
            elif row["pack_state"] == "opened" and (qty > capacity or product and qty < Decimal(row["handling_unit_count"]) * (Decimal(10) ** -product["quantity_precision"])):
                check.error("pack_quantity", "inventory_by_location", row["_row"], "opened quantity outside package-count bounds", warehouse)
            elif row["pack_state"] == "loose" and (physical["packaging_code"] != "base" or qty != row["handling_unit_count"]):
                check.error("pack_quantity", "inventory_by_location", row["_row"], "loose stock requires base package and one base unit per count", warehouse)
        if product:
            tracking = product["lot_tracking"]
            if tracking == "none" and row["lot_id"] is not None or tracking != "none" and row["lot_id"] is None:
                check.error("lot_id", "inventory_by_location", row["_row"], "lot_id inconsistent with SKU tracking", warehouse)
            lot_row = lot_by_pair.get((row["sku_id"], row["lot_id"])) if row["lot_id"] else None
            if row["lot_id"] and not lot_row:
                check.error("foreign_key", "inventory_by_location", row["_row"], "lot_id not in sku_lots", warehouse)
            if lot_row:
                if lot_row["disposition_at_snapshot"] is None:
                    check.error("lot_disposition", "sku_lots", lot_row["_row"], "current-stock lot needs disposition", warehouse)
                if tracking == "expiry" and (lot_row["expiry_at"] is None) == (lot_row["expiry_on"] is None):
                    check.error("lot_expiry", "sku_lots", lot_row["_row"], "current expiry-tracked lot needs exactly one expiry field", warehouse)
                expired = lot_row["expiry_at"] is not None and lot_row["expiry_at"] <= snapshot
                if lot_row["expiry_on"] and wh.get(warehouse):
                    try:
                        expired = snapshot.astimezone(_zone(wh[warehouse]["timezone"])).date() > lot_row["expiry_on"]
                    except (ValueError, ZoneInfoNotFoundError):
                        pass
                if row["stock_status"] == "available" and (lot_row["disposition_at_snapshot"] != "released" or expired):
                    check.error("lot_availability", "inventory_by_location", row["_row"], "available stock has held or expired lot", warehouse)
            if tracking == "serial":
                pair = (row["sku_id"], row["lot_id"])
                if pair in serial_keys:
                    check.error("serial_unique", "inventory_by_location", row["_row"], "serial occupies more than one inventory row", warehouse)
                serial_keys.add(pair)
                if row["on_hand_qty"] != 1 or not physical or physical["packaging_code"] != "base" or row["handling_unit_count"] != 1:
                    check.error("serial_package", "inventory_by_location", row["_row"], "v1 requires an individually counted base package per serial", warehouse)
            precision = product["quantity_precision"]
            if isinstance(precision, int) and 0 <= precision <= 6:
                step = Decimal(10) ** -precision
                for key in ("on_hand_qty", "reserved_qty", "hold_qty"):
                    if row[key] is not None and row[key] % step:
                        check.error("quantity_precision", "inventory_by_location", row["_row"], f"{key} exceeds SKU precision", warehouse)
        composite = tuple(row[k] for k in ("warehouse_id", "location_id", "sku_id", "lot_id", "packaging_profile_id", "pack_state", "stock_status"))
        if composite in inventory_keys:
            check.error("inventory_key", "inventory_by_location", row["_row"], "duplicate canonical logical stock key; aggregate source split rows", warehouse)
        inventory_keys.add(composite)

    active_assignments = set()
    for row in tables["slot_assignments"]:
        warehouse = row["warehouse_id"]
        _fk(check, "slot_assignments", row, "warehouse_id", wh)
        location = _fk(check, "slot_assignments", row, "location_id", loc)
        _same_warehouse(check, "slot_assignments", row, location)
        _fk(check, "slot_assignments", row, "sku_id", sku)
        if row["snapshot_at"] != snapshot:
            check.error("snapshot_time", "slot_assignments", row["_row"],
                        f"row snapshot_at={_timestamp_text(row['snapshot_at'])} differs from {snapshot_label}={_timestamp_text(snapshot)}", warehouse)
        _time_pair(check, "slot_assignments", row, "valid_from", "valid_to", strict=True)
        if row["valid_to"] and not row["valid_from"]:
            check.error("assignment_date", "slot_assignments", row["_row"], "valid_to requires valid_from", warehouse)
        if row["last_changed_at"] and row["last_changed_at"] > snapshot:
            check.error("assignment_date", "slot_assignments", row["_row"], "last_changed_at after snapshot", warehouse)
        if row["status"] == "active":
            key = (warehouse, row["sku_id"], row["location_id"])
            if key in active_assignments:
                check.error("assignment_key", "slot_assignments", row["_row"], "duplicate active warehouse/SKU/bin assignment", warehouse)
            active_assignments.add(key)
            if row["valid_from"] and not _effective(row, snapshot):
                check.error("assignment_date", "slot_assignments", row["_row"], "active assignment does not cover snapshot", warehouse)
            if location and row["role"] in {"pick", "both"} and location["location_type"] not in {"pick", "both"}:
                check.error("assignment_role", "slot_assignments", row["_row"], "pick role in non-pick location", warehouse)
        for key in ("min_pick_qty", "max_pick_qty"):
            _range(check, "slot_assignments", row, key)
        if row["min_pick_qty"] is not None and row["max_pick_qty"] is not None and row["min_pick_qty"] > row["max_pick_qty"]:
            check.error("pick_bounds", "slot_assignments", row["_row"], "min_pick_qty > max_pick_qty", warehouse)
        _range(check, "slot_assignments", row, "pick_priority", minimum=1)

    for name in ("location_pack_capacities", "shared_bin_plans", "co_storage_rules", "location_hazard_permissions", "hazard_segregation_rules", "hazard_separation_evidence"):
        for row in tables[name]:
            warehouse = row["warehouse_id"]
            _fk(check, name, row, "warehouse_id", wh)
            if "location_id" in row:
                _same_warehouse(check, name, row, _fk(check, name, row, "location_id", loc))
            if name == "location_pack_capacities":
                physical = _fk(check, name, row, "packaging_profile_id", pkg)
                if physical and (physical["warehouse_id"] not in (None, warehouse) or not _effective(physical, snapshot)):
                    check.error("package_scope", name, row["_row"], "capacity references wrong-scope or ineffective package", warehouse)
                _range(check, name, row, "max_handling_unit_count", minimum=1)
            if name == "hazard_separation_evidence":
                a = _fk(check, name, row, "location_a_id", loc)
                b = _fk(check, name, row, "location_b_id", loc)
                _same_warehouse(check, name, row, a)
                _same_warehouse(check, name, row, b)
                if row["location_a_id"] == row["location_b_id"]:
                    check.error("location_pair", name, row["_row"], "separation evidence needs distinct locations", warehouse)
                _range(check, name, row, "physical_clearance_m")
            if name == "hazard_segregation_rules":
                _range(check, name, row, "minimum_clearance_m")
                if row["barrier_required"] and not row["required_barrier_code"] or not row["barrier_required"] and row["required_barrier_code"]:
                    check.error("hazard_rule", name, row["_row"], "barrier code/requirement mismatch", warehouse)
                if row["allow_same_location"] and (row["minimum_clearance_m"] > 0 or row["barrier_required"] or row["prohibit_same_zone"]):
                    check.error("hazard_rule", name, row["_row"], "same-location permission contradicts separation requirements", warehouse)
            if name in {"co_storage_rules", "hazard_segregation_rules"}:
                _time_pair(check, name, row, "effective_from", "effective_to", strict=True)
            if "certified_at" in row and row["certified_at"] > snapshot or "surveyed_at" in row and row["surveyed_at"] > snapshot:
                check.error("certificate_time", name, row["_row"], "certificate/survey after snapshot", warehouse)
            if "valid_until" in row and row["valid_until"] is not None and row["valid_until"] <= snapshot:
                check.error("certificate_time", name, row["_row"], "certificate expired at snapshot", warehouse)
    for row in tables["shared_bin_plan_items"]:
        parent = _fk(check, "shared_bin_plan_items", row, "plan_id", plan)
        physical = _fk(check, "shared_bin_plan_items", row, "packaging_profile_id", pkg)
        if parent and physical and (physical["warehouse_id"] not in (None, parent["warehouse_id"]) or not _effective(physical, snapshot)):
            check.error("package_scope", "shared_bin_plan_items", row["_row"], "plan uses wrong-scope or ineffective package", parent["warehouse_id"])
        _range(check, "shared_bin_plan_items", row, "max_handling_unit_count", minimum=1)

    for name, keyfunc in (
        ("location_pack_capacities", lambda r: (r["location_id"], r["packaging_profile_id"])),
        ("shared_bin_plan_items", lambda r: (r["plan_id"], r["packaging_profile_id"])),
        ("location_hazard_permissions", lambda r: (r["location_id"], r["hazard_group"])),
        ("hazard_separation_evidence", lambda r: (r["warehouse_id"], *sorted((r["location_a_id"], r["location_b_id"])))),
    ):
        seen = set()
        for row in tables[name]:
            key = keyfunc(row)
            if key in seen:
                check.error("composite_key", name, row["_row"], f"duplicate composite key {key}", row.get("warehouse_id"))
            seen.add(key)

    for row in tables["aisle_nodes"]:
        _fk(check, "aisle_nodes", row, "warehouse_id", wh)
        _require(check, "aisle_nodes", row, (row["x_m"] is None) == (row["y_m"] is None), "x_m and y_m must both be present or blank")
    for row in tables["aisle_edges"]:
        _fk(check, "aisle_edges", row, "warehouse_id", wh)
        a = _fk(check, "aisle_edges", row, "from_node_id", node)
        b = _fk(check, "aisle_edges", row, "to_node_id", node)
        _same_warehouse(check, "aisle_edges", row, a)
        _same_warehouse(check, "aisle_edges", row, b)
        _range(check, "aisle_edges", row, "length_m", strict=True)
        if row["from_node_id"] == row["to_node_id"]:
            check.error("graph_edge", "aisle_edges", row["_row"], "self-loop not allowed", row["warehouse_id"])
        if a and b and row["level_change"] is not None and row["level_change"] != b["floor_level"] - a["floor_level"]:
            check.error("graph_level", "aisle_edges", row["_row"], "level_change differs from endpoint levels", row["warehouse_id"])
    for row in tables["location_access"]:
        _fk(check, "location_access", row, "warehouse_id", wh)
        _same_warehouse(check, "location_access", row, _fk(check, "location_access", row, "location_id", loc))
        _same_warehouse(check, "location_access", row, _fk(check, "location_access", row, "node_id", node))
        _range(check, "location_access", row, "walk_offset_m")

    hist_locs = {**loc, **hist}
    pick_sequences = set()
    for row in tables["pick_events"]:
        warehouse = row["warehouse_id"]
        _fk(check, "pick_events", row, "warehouse_id", wh)
        product = _fk(check, "pick_events", row, "sku_id", sku)
        line = _fk(check, "pick_events", row, "order_line_id", lines, optional=True)
        location = _fk(check, "pick_events", row, "location_id", hist_locs, optional=True)
        _same_warehouse(check, "pick_events", row, location)
        if line:
            if line["sku_id"] != row["sku_id"] or orders.get(line["order_id"], {}).get("warehouse_id") != warehouse:
                check.error("pick_line", "pick_events", row["_row"], "line SKU/warehouse does not match task", warehouse)
            order = orders.get(line["order_id"])
            if order:
                if order["ordered_at"] and row["event_at"] < order["ordered_at"]:
                    check.error("pick_time", "pick_events", row["_row"], "pick precedes order", warehouse)
                if order["ordered_on"] and wh.get(warehouse):
                    try:
                        if row["event_at"].astimezone(_zone(wh[warehouse]["timezone"])).date() < order["ordered_on"]:
                            check.error("pick_time", "pick_events", row["_row"], "pick date precedes order date", warehouse)
                    except (ValueError, ZoneInfoNotFoundError):
                        pass
                if row["started_at"] and order["ordered_at"] and row["started_at"] < order["ordered_at"]:
                    check.error("pick_time", "pick_events", row["_row"], "task starts before order", warehouse)
        else:
            check.warn("partial_line_history", f"pick_events row {row['_row']} has no order line; line-level reconciliation unavailable")
        if row["lot_id"]:
            if (row["sku_id"], row["lot_id"]) not in lot_by_pair:
                check.error("foreign_key", "pick_events", row["_row"], "lot_id does not resolve with SKU", warehouse)
        elif product and product["lot_tracking"] != "none":
            check.warn("partial_lot_history", f"pick_events row {row['_row']} has no lot; FEFO validation unavailable")
        if product and product["lot_tracking"] == "none" and row["lot_id"]:
            check.error("lot_id", "pick_events", row["_row"], "untracked SKU has lot ID", warehouse)
        if row["status"] == "completed":
            _require(check, "pick_events", row, row["picked_qty"] is not None and row["picked_qty"] > 0 and row["location_id"] is not None, "completed pick needs positive quantity and bin")
        _range(check, "pick_events", row, "picked_qty")
        _time_pair(check, "pick_events", row, "started_at", "event_at")
        _range(check, "pick_events", row, "route_sequence", minimum=1)
        _range(check, "pick_events", row, "distance_m")
        if (row["distance_m"] is None) != (row["distance_method"] is None):
            check.error("distance_method", "pick_events", row["_row"], "distance and method must occur together", warehouse)
        if row["route_sequence"] is not None:
            if not row["route_id"]:
                check.error("route_sequence", "pick_events", row["_row"], "sequence requires route_id", warehouse)
            key = (warehouse, row["route_id"], row["route_sequence"])
            if key in pick_sequences:
                check.error("route_sequence", "pick_events", row["_row"], "duplicate step within route instance", warehouse)
            pick_sequences.add(key)
        if isinstance(manifest.get("history_from"), datetime) and not manifest["history_from"] <= row["event_at"] < manifest["history_to"]:
            check.error("history_window", "pick_events", row["_row"], "event outside declared history window", warehouse)
        if product and 0 <= product["quantity_precision"] <= 6 and row["picked_qty"] % (Decimal(10) ** -product["quantity_precision"]):
            check.error("quantity_precision", "pick_events", row["_row"], "picked_qty exceeds SKU precision", warehouse)

    for row in tables["replenishment_events"]:
        warehouse = row["warehouse_id"]
        _fk(check, "replenishment_events", row, "warehouse_id", wh)
        product = _fk(check, "replenishment_events", row, "sku_id", sku)
        source = _fk(check, "replenishment_events", row, "source_location_id", hist_locs, optional=True)
        dest = _fk(check, "replenishment_events", row, "destination_location_id", hist_locs, optional=True)
        source_node = _fk(check, "replenishment_events", row, "source_node_id", node, optional=True)
        for target in (source, dest, source_node):
            _same_warehouse(check, "replenishment_events", row, target)
        if source and source_node and source["location_id"] in loc:
            if not any(access["location_id"] == source["location_id"] and access["node_id"] == source_node["node_id"] for access in tables["location_access"]):
                check.error("replenishment_source", "replenishment_events", row["_row"], "source_node_id is not an access point for source_location_id", warehouse)
        if row["lot_id"] and (row["sku_id"], row["lot_id"]) not in lot_by_pair:
            check.error("foreign_key", "replenishment_events", row["_row"], "lot_id does not resolve with SKU", warehouse)
        if product and product["lot_tracking"] == "none" and row["lot_id"]:
            check.error("lot_id", "replenishment_events", row["_row"], "untracked SKU has lot ID", warehouse)
        if row["status"] == "completed":
            _require(check, "replenishment_events", row, row["moved_qty"] is not None and row["moved_qty"] > 0 and dest is not None and row["completed_at"] == row["event_at"], "completed task needs positive quantity, destination and matching completion time")
        elif row["completed_at"] is not None:
            check.error("replenishment_time", "replenishment_events", row["_row"], "non-completed task has completed_at", warehouse)
        _range(check, "replenishment_events", row, "moved_qty")
        _time_pair(check, "replenishment_events", row, "requested_at", "started_at")
        _time_pair(check, "replenishment_events", row, "requested_at", "event_at")
        _time_pair(check, "replenishment_events", row, "started_at", "event_at")
        if row["event_at"] > snapshot or isinstance(manifest.get("history_from"), datetime) and not manifest["history_from"] <= row["event_at"] < manifest["history_to"]:
            check.error("history_window", "replenishment_events", row["_row"], "event outside snapshot/history window", warehouse)
        _range(check, "replenishment_events", row, "distance_m")
        if (row["distance_m"] is None) != (row["distance_method"] is None):
            check.error("distance_method", "replenishment_events", row["_row"], "distance and method must occur together", warehouse)
        if product and 0 <= product["quantity_precision"] <= 6 and row["moved_qty"] % (Decimal(10) ** -product["quantity_precision"]):
            check.error("quantity_precision", "replenishment_events", row["_row"], "moved_qty exceeds SKU precision", warehouse)
    for row in tables["order_lines"]:
        product = sku.get(row["sku_id"])
        if product and 0 <= product["quantity_precision"] <= 6:
            step = Decimal(10) ** -product["quantity_precision"]
            for key in ("ordered_qty", "fulfilled_qty"):
                if row[key] is not None and row[key] % step:
                    check.error("quantity_precision", "order_lines", row["_row"], f"{key} exceeds SKU precision")
    for row in tables["slot_assignments"]:
        product = sku.get(row["sku_id"])
        if product and 0 <= product["quantity_precision"] <= 6:
            step = Decimal(10) ** -product["quantity_precision"]
            for key in ("min_pick_qty", "max_pick_qty"):
                if row[key] is not None and row[key] % step:
                    check.error("quantity_precision", "slot_assignments", row["_row"], f"{key} exceeds SKU precision", row["warehouse_id"])


def _orientations(package: dict) -> list[tuple[Decimal, Decimal, Decimal]]:
    a, b, c = (package[k] for k in ("outer_length_cm", "outer_width_cm", "outer_height_cm"))
    variants = [(a, b, c)]
    if package["orientation_policy"] in {"rotate_horizontal", "rotate_any"}:
        variants.append((b, a, c))
    if package["orientation_policy"] == "rotate_any":
        variants.extend([(a, c, b), (c, a, b), (b, c, a), (c, b, a)])
    return variants


def _fits(package: dict, location: dict) -> bool:
    dimensions = [location.get(k) for k in ("usable_length_cm", "usable_width_cm", "usable_height_cm")]
    if any(x is None for x in dimensions):
        return False
    return any(all(size <= limit for size, limit in zip(variant, dimensions)) for variant in _orientations(package))


def _optimistic_pack_limit(package: dict, location: dict) -> int:
    """Necessary loose upper bound; certification still establishes actual packability."""
    dimensions = [location.get(k) for k in ("usable_length_cm", "usable_width_cm", "usable_height_cm")]
    if any(x is None for x in dimensions):
        return 0
    length, width, height = dimensions
    best = 0
    for a, b, c in _orientations(package):
        if a > length or b > width or c > height:
            continue
        floor_units = int((length * width) // (a * b))
        layers = 1
        if package["stackable"]:
            if package["max_stack_layers"] is None or package["max_supported_top_load_kg"] is None or package["gross_weight_kg"] <= 0:
                return 0
            layers = min(package["max_stack_layers"], int(height // c),
                         int(package["max_supported_top_load_kg"] // package["gross_weight_kg"]) + 1)
        best = max(best, floor_units * layers)
    return best


def _capacity_excess(package: dict, location: dict, count: int) -> list[str]:
    weight = Decimal(count) * package["gross_weight_kg"]
    volume = Decimal(count) * package["outer_length_cm"] * package["outer_width_cm"] * package["outer_height_cm"]
    limit = _optimistic_pack_limit(package, location)
    reasons = []
    if location["max_total_weight_kg"] is not None and weight > location["max_total_weight_kg"]:
        reasons.append(f"weight {weight} > {location['max_total_weight_kg']} kg")
    if location["usable_volume_cm3"] is not None and volume > location["usable_volume_cm3"]:
        reasons.append(f"volume {volume} > {location['usable_volume_cm3']} cm3")
    if count > limit:
        reasons.append(f"geometry upper bound {limit} packs < certified {count}")
    return reasons


def _reachable(tables: dict, warehouse: str, start: str, target: str, mode: str) -> bool:
    if start == target:
        return True
    nodes = {r["node_id"] for r in tables["aisle_nodes"] if r["warehouse_id"] == warehouse and r["status"] == "active"}
    if start not in nodes or target not in nodes:
        return False
    links = defaultdict(list)
    for edge in tables["aisle_edges"]:
        if edge["warehouse_id"] != warehouse or edge["status"] != "active" or edge["travel_mode"] not in (mode, "both"):
            continue
        if edge["from_node_id"] in nodes and edge["to_node_id"] in nodes:
            links[edge["from_node_id"]].append(edge["to_node_id"])
            if edge["bidirectional"]:
                links[edge["to_node_id"]].append(edge["from_node_id"])
    seen, pending = {start}, [start]
    for current in pending:
        for neighbor in links[current]:
            if neighbor == target:
                return True
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return False


def _validate_physical(manifest: dict, tables: dict[str, list[dict]], check: _Check) -> dict[str, list[str]]:
    if manifest.get("import_kind") != "full_snapshot" or not isinstance(manifest.get("snapshot_at"), datetime):
        return {}
    snapshot = manifest["snapshot_at"]
    wh = _index(tables["warehouses"], "warehouse_id")
    zone = _index(tables["zones"], "zone_id")
    loc = _index(tables["locations"], "location_id")
    pkg = _index(tables["sku_packaging_profiles"], "packaging_profile_id")
    sku = _index(tables["skus"], "sku_id")
    stock = defaultdict(list)
    for row in tables["inventory_by_location"]:
        stock[row["location_id"]].append(row)
    assigned = defaultdict(set)
    for row in tables["slot_assignments"]:
        if row["status"] == "active":
            assigned[row["location_id"]].add(row["sku_id"])
    caps = defaultdict(list)
    for row in tables["location_pack_capacities"]:
        caps[(row["location_id"], row["packaging_profile_id"])].append(row)
    plan_items = defaultdict(dict)
    for row in tables["shared_bin_plan_items"]:
        plan_items[row["plan_id"]][row["packaging_profile_id"]] = row["max_handling_unit_count"]
    plans = defaultdict(list)
    for row in tables["shared_bin_plans"]:
        plans[row["location_id"]].append(row)
    permissions = {(r["location_id"], r["hazard_group"]) for r in tables["location_hazard_permissions"]}
    co_rules = {(r["warehouse_id"], *sorted((r["group_a"], r["group_b"]))): r for r in tables["co_storage_rules"] if _effective(r, snapshot)}
    hazard_rules = {(r["warehouse_id"], *sorted((r["hazard_group_a"], r["hazard_group_b"]))): r for r in tables["hazard_segregation_rules"] if _effective(r, snapshot)}
    separation = {(r["warehouse_id"], *sorted((r["location_a_id"], r["location_b_id"]))): r for r in tables["hazard_separation_evidence"]}
    current_groups = defaultdict(lambda: defaultdict(set))
    candidate = defaultdict(list)
    for location_id, location in loc.items():
        warehouse = location["warehouse_id"]
        facility = wh.get(warehouse)
        parent_zone = zone.get(location["zone_id"])
        is_candidate = bool(facility and facility["status"] == "active" and parent_zone and parent_zone["status"] == "active" and location["status"] == "active" and location["blocked"] is False and location["location_type"] in {"pick", "both"})
        if not is_candidate:
            check.blocked[warehouse][location_id].add("not_candidate")
        measurements = ("usable_length_cm", "usable_width_cm", "usable_height_cm", "usable_volume_cm3", "max_total_weight_kg", "max_distinct_skus", "operating_temp_min_c", "operating_temp_max_c")
        if is_candidate and any(location[k] is None for k in measurements):
            check.incident("unmeasured_location", warehouse, location_id, "candidate bin lacks required measured capacity or temperature fields")
        rows = stock[location_id]
        present_skus = {r["sku_id"] for r in rows} | assigned[location_id]
        if location["max_distinct_skus"] is not None and len(present_skus) > location["max_distinct_skus"]:
            check.incident("sku_limit", warehouse, location_id, "physical/assigned distinct SKU count exceeds bin limit")
        if is_candidate and location["max_distinct_skus"] == 0:
            check.incident("sku_limit", warehouse, location_id, "bin policy permits no SKU placement")
        if any(sku.get(r["sku_id"], {}).get("lot_tracking") != "none" for r in rows) and is_candidate and (location["allow_mixed_lots"] is None or location["max_lots_per_sku"] is None):
            check.incident("unknown_lot_policy", warehouse, location_id, "tracked stock needs explicit lot-mixing limits")
        by_sku_lots = defaultdict(set)
        package_counts = defaultdict(int)
        total_weight = Decimal(0)
        total_volume = Decimal(0)
        known_physical = True
        profiles = {sid: _profile_for(tables, sid, warehouse, snapshot) for sid in present_skus}
        for sid, profile in profiles.items():
            if profile is None:
                check.incident("profile_coverage", warehouse, location_id, f"SKU {sid} has no unique effective storage profile")
                continue
            if location["operating_temp_min_c"] is not None and location["operating_temp_max_c"] is not None:
                if location["operating_temp_min_c"] < profile["storage_temp_min_c"] or location["operating_temp_max_c"] > profile["storage_temp_max_c"]:
                    check.incident("temperature", warehouse, location_id, f"SKU {sid} storage interval not met")
            if profile["temperature_class"] == "controlled" and location["temperature_class"] != "controlled":
                check.incident("controlled_storage", warehouse, location_id, f"SKU {sid} needs controlled location")
            if (location_id, profile["hazard_group"]) not in permissions:
                check.incident("hazard_permission", warehouse, location_id, f"no approved permission for {profile['hazard_group']}")
            current_groups[warehouse][location_id].add(profile["hazard_group"])
        for row in rows:
            product = sku.get(row["sku_id"])
            physical = pkg.get(row["packaging_profile_id"])
            if product and product["lot_tracking"] != "none" and row["lot_id"]:
                by_sku_lots[row["sku_id"]].add(row["lot_id"])
            if not physical or row["handling_unit_count"] is None:
                known_physical = False
                continue
            count = row["handling_unit_count"]
            package_counts[physical["packaging_profile_id"]] += count
            total_weight += Decimal(count) * physical["gross_weight_kg"]
            total_volume += Decimal(count) * physical["outer_length_cm"] * physical["outer_width_cm"] * physical["outer_height_cm"]
            if not _fits(physical, location):
                check.incident("individual_fit", warehouse, location_id, f"{physical['packaging_profile_id']} cannot fit in allowed orientation")
        if not known_physical:
            check.incident("unknown_package", warehouse, location_id, "physical package or count missing")
        if location["max_lots_per_sku"] is not None:
            for sku_id, lots in by_sku_lots.items():
                if len(lots) > location["max_lots_per_sku"] or location["allow_mixed_lots"] is False and len(lots) > 1:
                    check.incident("lot_limit", warehouse, location_id, f"SKU {sku_id} exceeds lot-mixing limit")
        if location["max_total_weight_kg"] is not None and total_weight > location["max_total_weight_kg"]:
            check.incident("weight_capacity", warehouse, location_id, f"occupied {total_weight} kg > {location['max_total_weight_kg']} kg")
        if location["usable_volume_cm3"] is not None and total_volume > location["usable_volume_cm3"]:
            check.incident("volume_capacity", warehouse, location_id, f"occupied {total_volume} cm3 > {location['usable_volume_cm3']} cm3")
        if len(package_counts) == 1:
            profile_id, count = next(iter(package_counts.items()))
            valid_caps = [c for c in caps[(location_id, profile_id)] if c["certified_at"] <= snapshot and (c["valid_until"] is None or snapshot < c["valid_until"]) and not _capacity_excess(pkg[profile_id], location, c["max_handling_unit_count"])]
            if not valid_caps and is_candidate:
                check.incident("no_pack_capacity", warehouse, location_id, "no certified single-package capacity; a validated packing solver is not configured")
            elif valid_caps and count > valid_caps[0]["max_handling_unit_count"]:
                check.incident("pack_capacity", warehouse, location_id, "physical package count exceeds certified limit")
        elif len(package_counts) > 1:
            matching = [p for p in plans[location_id] if p["certified_at"] <= snapshot and (p["valid_until"] is None or snapshot < p["valid_until"]) and all(plan_items[p["plan_id"]].get(pid, -1) >= count for pid, count in package_counts.items())]
            if not matching:
                check.incident("joint_packing", warehouse, location_id, "no approved simultaneous mixed-package plan")
        for approved in plans[location_id]:
            items = plan_items[approved["plan_id"]]
            if not items:
                check.incident("empty_joint_plan", warehouse, location_id, "shared-bin plan has no packages")
                continue
            plan_weight = sum((Decimal(count) * pkg[pid]["gross_weight_kg"] for pid, count in items.items() if pid in pkg), Decimal(0))
            plan_volume = sum((Decimal(count) * pkg[pid]["outer_length_cm"] * pkg[pid]["outer_width_cm"] * pkg[pid]["outer_height_cm"] for pid, count in items.items() if pid in pkg), Decimal(0))
            if location["max_total_weight_kg"] is not None and plan_weight > location["max_total_weight_kg"] or location["usable_volume_cm3"] is not None and plan_volume > location["usable_volume_cm3"]:
                check.incident("unsafe_joint_plan", warehouse, location_id, "plan maxima exceed measured weight or volume capacity")
            if any(pid in pkg and not _fits(pkg[pid], location) for pid in items):
                check.incident("unsafe_joint_plan", warehouse, location_id, "plan includes a package that cannot fit")
            if any(pid in pkg and count > _optimistic_pack_limit(pkg[pid], location) for pid, count in items.items()):
                check.incident("unsafe_joint_plan", warehouse, location_id, "plan maxima exceed orientation, floor-space or stack upper bound")
        for profile_id, approved_rows in ((pid, caps[(location_id, pid)]) for pid in {key[1] for key in caps if key[0] == location_id}):
            physical = pkg.get(profile_id)
            if not physical:
                continue
            for approved in approved_rows:
                count = approved["max_handling_unit_count"]
                exceeded = _capacity_excess(physical, location, count)
                if exceeded:
                    check.warn("unsafe_pack_capacity", f"{location_id}: certificate for {profile_id} excluded: {'; '.join(exceeded)}")
        if len(present_skus) > 1:
            for first in sorted(present_skus):
                for second in sorted(present_skus):
                    if first >= second or not profiles[first] or not profiles[second]:
                        continue
                    key = (warehouse, *sorted((profiles[first]["co_storage_group"], profiles[second]["co_storage_group"])))
                    if not co_rules.get(key, {}).get("allowed"):
                        check.incident("co_storage", warehouse, location_id, f"no approved shared-bin pair for {first} and {second}")
        if is_candidate:
            start = facility["default_pick_node_id"] if facility else None
            accesses = [a for a in tables["location_access"] if a["location_id"] == location_id and a["status"] == "active" and a["travel_mode"] in {"picker", "both"}]
            if not start or not any(_reachable(tables, warehouse, start, a["node_id"], "picker") for a in accesses):
                check.incident("route_unreachable", warehouse, location_id, "no active picker path from default depot to pick face")
            has_valid_capacity = any(key[0] == location_id and pkg.get(key[1]) and any(
                approved["certified_at"] <= snapshot and (approved["valid_until"] is None or snapshot < approved["valid_until"])
                and not _capacity_excess(pkg[key[1]], location, approved["max_handling_unit_count"])
                for approved in approved_rows) for key, approved_rows in caps.items())
            if not rows and not has_valid_capacity and not plans[location_id]:
                check.incident("no_pack_capacity", warehouse, location_id, "no certified package capacity for an empty candidate bin")
            if not check.blocked[warehouse][location_id]:
                candidate[warehouse].append(location_id)

    for warehouse, locations in current_groups.items():
        all_groups = sorted(set().union(*locations.values())) if locations else []
        for i, group_a in enumerate(all_groups):
            for group_b in all_groups[i:]:
                rule = hazard_rules.get((warehouse, *sorted((group_a, group_b))))
                if rule is None:
                    for location_id in locations:
                        if group_a in locations[location_id] or group_b in locations[location_id]:
                            check.incident("hazard_rule_missing", warehouse, location_id, f"missing rule for {group_a}/{group_b}")
        location_ids = sorted(locations)
        for i, a in enumerate(location_ids):
            for b in location_ids[i:]:
                for group_a in locations[a]:
                    for group_b in locations[b]:
                        rule = hazard_rules.get((warehouse, *sorted((group_a, group_b))))
                        if not rule:
                            continue
                        if a == b:
                            if not rule["allow_same_location"] and (group_a != group_b or len([sid for sid in present_skus if profiles.get(sid) and profiles[sid]["hazard_group"] == group_a]) > 1):
                                check.incident("hazard_same_bin", warehouse, a, f"{group_a}/{group_b} may not co-locate")
                            continue
                        if rule["prohibit_same_zone"] and loc[a]["zone_id"] == loc[b]["zone_id"]:
                            for location_id in (a, b):
                                check.incident("hazard_same_zone", warehouse, location_id, f"{group_a}/{group_b} must be in separate zones")
                        if rule["minimum_clearance_m"] > 0 or rule["barrier_required"]:
                            evidence = separation.get((warehouse, *sorted((a, b))))
                            if not evidence or evidence["physical_clearance_m"] < rule["minimum_clearance_m"] or rule["barrier_required"] and evidence["barrier_code"] != rule["required_barrier_code"]:
                                for location_id in (a, b):
                                    check.incident("hazard_separation", warehouse, location_id, f"missing/insufficient physical separation for {group_a}/{group_b}")
    return {warehouse: sorted(set(ids) - {location for location, reasons in check.blocked[warehouse].items() if reasons}) for warehouse, ids in candidate.items()}


def import_package(path: Path | str) -> ImportResult:
    """Parse canonical CSVs, report hard errors and candidate-level safety incidents.

    Evidence references are checked for shape/consistency but not cryptographically
    verified here. This function does not publish data or call the optimizer.
    """
    path = Path(path)
    check = _Check()
    if not path.is_dir():
        check.error("package_path", "manifest", None, f"not a directory: {path}")
        return ImportResult({}, {name: [] for name in TABLES}, _report({}, {}, check, {}))
    manifest, tables = _load(path, check)
    if manifest and not check.errors:
        _validate_relations(manifest, tables, check)
    eligible = _validate_physical(manifest, tables, check) if manifest and not check.errors else {}
    check.warn("external_evidence", "source snapshot tokens, quiescence attestations, site certificates and lineage references require independent trust/approval before operational release")
    return ImportResult(manifest, tables, _report(manifest, tables, check, eligible))


def _report(manifest: dict, tables: dict, check: _Check, eligible: dict[str, list[str]]) -> dict:
    warehouses = {}
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    for warehouse in manifest.get("warehouse_ids", []) if isinstance(manifest.get("warehouse_ids"), list) else []:
        if not isinstance(warehouse, str):
            continue
        warehouses[warehouse] = {
            "candidate_locations": eligible.get(warehouse, []) if not check.errors else [],
            "blocked_locations": {location: sorted(reasons) for location, reasons in check.blocked[warehouse].items() if reasons},
            "accepted_for_recommendations": False,
            "release_reason": "external evidence verification and optimizer integration are not implemented",
        }
    return {
        "schema_version": manifest.get("schema_version"),
        "dataset_id": manifest.get("dataset_id"),
        "import_kind": manifest.get("import_kind"),
        "valid": not check.errors,
        "errors": check.errors,
        "warnings": check.warnings,
        "incidents": check.incidents,
        "row_counts": {name: len(rows) for name, rows in tables.items() if rows or f"{name}.csv" in files},
        "warehouses": warehouses,
        "optimizer_invoked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a WMS-neutral Slotwise CSV package; does not run optimizer")
    parser.add_argument("package", type=Path)
    parser.add_argument("--report", type=Path, help="write the validation report as JSON")
    args = parser.parse_args()
    result = import_package(args.package)
    output = json.dumps(result.report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
