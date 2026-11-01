import tempfile
import unittest
import csv
import hashlib
import json
import re
from pathlib import Path

from backend.canonical_import import import_package
from backend.canonical_schema import TABLES
from backend.tests.canonical_fixtures import record, shared_bin_rows, write_package


class CanonicalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / "package"

    def check(self, rows):
        write_package(self.package, rows)
        return import_package(self.package)

    def test_valid_full_import_with_shared_bin_and_partial_picks(self):
        result = self.check(shared_bin_rows())
        self.assertTrue(result.valid, result.report)
        self.assertEqual(result.report["warehouses"]["WH1"]["candidate_locations"], ["L1"])
        self.assertEqual(len(result.tables["inventory_by_location"]), 2)
        self.assertEqual(result.report["row_counts"]["pick_events"], 2)
        self.assertFalse(result.report["warehouses"]["WH1"]["accepted_for_recommendations"])
        self.assertFalse(result.report["optimizer_invoked"])

    def test_contract_headers_and_required_columns_match_schema(self):
        contract = (Path(__file__).resolve().parents[2] / "docs" / "WAREHOUSE_DATA_CONTRACT.md").read_text(encoding="utf-8")
        documented = {}
        for section in re.split(r"(?=^### )", contract, flags=re.M):
            names = re.findall(r"`([a-z_]+)\.csv`", section.splitlines()[0] if section else "")
            if not names:
                continue
            for line in section.splitlines():
                if not line.startswith("| "):
                    continue
                cells = [cell.strip() for cell in line.split("|")[1:-1]]
                if len(cells) < 3 or cells[2] not in {"R", "O", "C"}:
                    continue
                table = names[1] if cells[0].startswith("item:") and len(names) > 1 else names[0]
                for field in re.findall(r"`([a-z_][a-z_0-9]*)`", cells[0]):
                    documented.setdefault(table, {})[field] = cells[2]
        self.assertEqual(set(documented), set(TABLES))
        for table, columns in TABLES.items():
            self.assertEqual(set(documented[table]), {column.name for column in columns}, table)
            for column in columns:
                if documented[table][column.name] != "C":
                    self.assertEqual(documented[table][column.name] == "R", column.required, (table, column.name))

    def _replace_access_header(self, replacement: str, *, keep_canonical: bool = False):
        rows = shared_bin_rows()
        write_package(self.package, rows)
        file = self.package / "location_access.csv"
        with file.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            header = list(reader.fieldnames)
            records = list(reader)
        if keep_canonical:
            header.append(replacement)
            for row in records:
                row[replacement] = row["walk_offset_m"]
        else:
            header[header.index("walk_offset_m")] = replacement
            for row in records:
                row[replacement] = row.pop("walk_offset_m")
        with file.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        manifest_file = self.package / "manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["files"]["location_access.csv"]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    def test_contract_walk_offset_is_parsed_and_range_checked(self):
        rows = shared_bin_rows()
        rows["location_access"][0]["walk_offset_m"] = "2.5"
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertEqual(str(result.tables["location_access"][0]["walk_offset_m"]), "2.5")
        rows["location_access"][0]["walk_offset_m"] = "-1"
        result = self.check(rows)
        self.assertFalse(result.valid)
        self.assertTrue(any(e["table"] == "location_access" and e["code"] == "range" for e in result.report["errors"]))

    def test_missing_required_walk_offset_is_rejected_without_default(self):
        self._replace_access_header("unrecognized_offset")
        result = import_package(self.package)
        self.assertFalse(result.valid)
        self.assertTrue(any(e["table"] == "location_access" and e["code"] == "csv_headers" and "walk_offset_m" in e["detail"] for e in result.report["errors"]))

    def test_legacy_access_offset_package_is_normalized(self):
        self._replace_access_header("access_offset_m")
        result = import_package(self.package)
        self.assertTrue(result.valid, result.report)
        self.assertIn("walk_offset_m", result.tables["location_access"][0])
        self.assertNotIn("access_offset_m", result.tables["location_access"][0])
        self.assertIn("legacy_column_alias", {warning["code"] for warning in result.report["warnings"]})

    def test_both_offset_headers_are_rejected(self):
        self._replace_access_header("access_offset_m", keep_canonical=True)
        result = import_package(self.package)
        self.assertFalse(result.valid)
        self.assertIn("csv_headers", {error["code"] for error in result.report["errors"]})

    def test_invalid_current_reference_rejects_package(self):
        rows = shared_bin_rows()
        rows["inventory_by_location"][0]["location_id"] = "MISSING"
        result = self.check(rows)
        self.assertFalse(result.valid)
        self.assertIn("foreign_key", {error["code"] for error in result.report["errors"]})
        self.assertEqual(result.report["warehouses"]["WH1"]["candidate_locations"], [])

    def test_partial_history_without_orders_or_lot_link_is_accepted_with_limits(self):
        rows = shared_bin_rows()
        rows.pop("orders")
        rows.pop("order_lines")
        rows["pick_events"] = [record("pick_events", pick_event_id="PE1", warehouse_id="WH1", sku_id="S1", location_id="L1", picked_qty="1", event_at="2026-01-03T01:00:00Z", status="completed")]
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertIn("partial_lot_history", {warning["code"] for warning in result.report["warnings"]})
        self.assertIn("partial_line_history", {warning["code"] for warning in result.report["warnings"]})

    def test_demand_only_package_does_not_claim_slot_eligibility(self):
        full = shared_bin_rows()
        rows = {name: full[name] for name in ("warehouses", "skus", "orders", "order_lines")}
        rows["warehouses"][0]["default_pick_node_id"] = ""
        write_package(self.package, rows, mode="demand_only")
        result = import_package(self.package)
        self.assertTrue(result.valid, result.report)
        self.assertEqual(result.report["warehouses"]["WH1"]["candidate_locations"], [])
        self.assertFalse(result.report["warehouses"]["WH1"]["accepted_for_recommendations"])

    def test_multiple_warehouse_scope_is_kept_separate(self):
        rows = shared_bin_rows()
        rows["warehouses"].append(record("warehouses", warehouse_id="WH2", name="Second fixture warehouse", timezone="UTC", status="inactive"))
        rows["zones"].append(record("zones", zone_id="Z2", warehouse_id="WH2", name="Inactive zone", zone_type="ambient", status="inactive"))
        rows["locations"].append(record("locations", location_id="L2", warehouse_id="WH2", zone_id="Z2", location_type="reserve", status="inactive", blocked="true", temperature_class="ambient"))
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertEqual(result.report["warehouses"]["WH1"]["candidate_locations"], ["L1"])
        self.assertEqual(result.report["warehouses"]["WH2"]["candidate_locations"], [])

    def test_shared_bin_requires_approved_pair_and_joint_pack_plan(self):
        rows = shared_bin_rows()
        rows.pop("co_storage_rules")
        rows.pop("shared_bin_plans")
        rows.pop("shared_bin_plan_items")
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        blocked = result.report["warehouses"]["WH1"]["blocked_locations"]["L1"]
        self.assertIn("co_storage", blocked)
        self.assertIn("joint_packing", blocked)

    def test_unsafe_location_retains_stock_but_blocks_placement(self):
        rows = shared_bin_rows()
        rows["locations"][0]["max_total_weight_kg"] = "5"
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertEqual(len(result.tables["inventory_by_location"]), 2)
        self.assertIn("weight_capacity", result.report["warehouses"]["WH1"]["blocked_locations"]["L1"])
        self.assertEqual(result.report["warehouses"]["WH1"]["candidate_locations"], [])

    def test_unsafe_joint_plan_is_blocked_even_when_current_stock_fits(self):
        rows = shared_bin_rows()
        rows["shared_bin_plan_items"][0]["max_handling_unit_count"] = "1000"
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertIn("unsafe_joint_plan", result.report["warehouses"]["WH1"]["blocked_locations"]["L1"])

    def test_same_hazard_group_must_allow_shared_bin(self):
        rows = shared_bin_rows()
        rows["hazard_segregation_rules"][0]["allow_same_location"] = "false"
        result = self.check(rows)
        self.assertTrue(result.valid, result.report)
        self.assertIn("hazard_same_bin", result.report["warehouses"]["WH1"]["blocked_locations"]["L1"])

    def test_checksum_tampering_is_rejected(self):
        rows = shared_bin_rows()
        write_package(self.package, rows)
        file = self.package / "skus.csv"
        file.write_text(file.read_text(encoding="utf-8").replace("Synthetic item one", "Changed item"), encoding="utf-8")
        result = import_package(self.package)
        self.assertFalse(result.valid)
        self.assertIn("checksum", {error["code"] for error in result.report["errors"]})


if __name__ == "__main__":
    unittest.main()
