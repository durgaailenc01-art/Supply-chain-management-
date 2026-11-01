import tempfile
import unittest
import io
import csv
import json
import asyncio
from collections import Counter
from pathlib import Path

from backend.tests.canonical_fixtures import SNAPSHOT, record, shared_bin_rows, write_package
from backend.live_upload import run_upload
from backend.live_engine import _paths
from backend.live_evaluation import evaluate


class LiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def files(self, rows, *, manifest=True):
        write_package(self.folder, rows)
        return [(p.name, p.read_bytes()) for p in self.folder.iterdir() if p.suffix == ".csv" or manifest and p.name == "manifest.json"]

    def test_observed_evaluation_requires_independent_post_snapshot_rows(self):
        result = {"snapshot_at": SNAPSHOT, "recommendations": [{"sku_id": "S1", "proposed_location_id": "L2", "proposed_round_trip_m": 10, "decision": "move"}, {"sku_id": "S2", "proposed_location_id": "L1", "proposed_round_trip_m": 20, "decision": "keep"}]}
        observed = b"sku_id,actual_location_id,observed_at,measured_round_trip_m\nS1,L2,2026-02-01T00:00:00Z,12\n"
        metrics = evaluate(result, observed)
        self.assertEqual(metrics["coverage_pct"], 50)
        self.assertEqual(metrics["move_agreement_pct"], 100)
        self.assertEqual(metrics["route_mae_m"], 2)
        with self.assertRaisesRegex(ValueError, "after the analysis snapshot"):
            evaluate(result, observed.replace(b"2026-02-01", b"2026-01-01"))

    def test_full_upload_produces_real_move_and_route_improvement(self):
        rows = shared_bin_rows()
        rows["locations"].append(record("locations", location_id="L2", warehouse_id="WH1", zone_id="Z1", location_type="pick", status="active", blocked="false", usable_length_cm="100", usable_width_cm="100", usable_height_cm="100", usable_volume_cm3="1000000", max_total_weight_kg="1000", max_distinct_skus="1", allow_mixed_lots="true", max_lots_per_sku="2", temperature_class="ambient", operating_temp_min_c="0", operating_temp_max_c="30"))
        rows["aisle_nodes"].append(record("aisle_nodes", node_id="N2", warehouse_id="WH1", node_type="location_access", floor_level="0", status="active"))
        rows["aisle_edges"].append(record("aisle_edges", edge_id="E2", warehouse_id="WH1", from_node_id="N0", to_node_id="N2", length_m="1", bidirectional="true", travel_mode="picker", status="active"))
        rows["location_access"].append(record("location_access", access_id="AC2", warehouse_id="WH1", location_id="L2", node_id="N2", walk_offset_m="1", travel_mode="picker", status="active"))
        rows["location_hazard_permissions"].append(record("location_hazard_permissions", permission_id="HP2", warehouse_id="WH1", location_id="L2", hazard_group="none"))
        rows["location_pack_capacities"] = [record("location_pack_capacities", capacity_id="C1", warehouse_id="WH1", location_id="L2", packaging_profile_id="PK1", max_handling_unit_count="4", certified_at="2026-01-30T00:00:00Z", basis="site_test", approval_ref="fixture-capacity")]
        result = run_upload(self.files(rows), "WH1", SNAPSHOT)
        by_sku = {row["sku_id"]: row for row in result["recommendations"]}
        self.assertEqual(by_sku["S1"]["current_location_id"], "L1")
        self.assertEqual(by_sku["S1"]["proposed_location_id"], "L2")
        self.assertEqual(by_sku["S1"]["decision"], "move")
        self.assertEqual(result["movement_lines"][0]["source_location_id"], "L1")
        self.assertEqual(result["movement_lines"][0]["proposed_location_id"], "L2")
        self.assertEqual(result["movement_lines"][0]["handling_unit_count"], 2)
        self.assertGreater(result["metrics"]["estimated_distance_improvement_pct"], 0)
        self.assertEqual(result["metrics"]["unassigned_skus"], 0)
        self.assertLessEqual(result["metrics"]["optimization_score"], result["metrics"]["theoretical_travel_score_ceiling_pct"])
        self.assertEqual(result["solver"]["status"], "optimal")
        self.assertEqual(result["solver"]["objective_gap_pct"], 0.0)

    def test_canonical_csv_without_manifest_is_provisional(self):
        result = run_upload(self.files(shared_bin_rows(), manifest=False), "Fixture warehouse", SNAPSHOT)
        self.assertEqual(result["status"], "provisional_analysis")
        self.assertIn("snapshot_unverified", {item["code"] for item in result["validation"]["warnings"]})

    def test_manifest_snapshot_mismatch_is_rejected_before_full_import(self):
        with self.assertRaisesRegex(ValueError, "differs from manifest.snapshot_at"):
            run_upload(self.files(shared_bin_rows()), "WH1", "2026-01-31T12:01:00Z")

    def test_manifest_warehouse_mismatch_names_available_warehouse(self):
        with self.assertRaisesRegex(ValueError, "Available: Fixture warehouse \\(WH1\\)"):
            run_upload(self.files(shared_bin_rows()), "WH-DEMO-01", SNAPSHOT)

    def test_invalid_certificate_does_not_block_other_valid_packing_evidence(self):
        rows = shared_bin_rows()
        rows["location_pack_capacities"] = [record("location_pack_capacities", capacity_id="BAD1", warehouse_id="WH1", location_id="L1", packaging_profile_id="PK1", max_handling_unit_count="1001", certified_at="2026-01-30T00:00:00Z", basis="site_test", approval_ref="invalid-fixture-capacity")]
        result = run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)
        self.assertEqual(result["statistics"]["candidate_locations"], 1)
        self.assertEqual(result["metrics"]["assigned_skus"], 2)
        self.assertIn("unsafe_pack_capacity", {warning["code"] for warning in result["validation"]["warnings"]})

    def test_expired_shared_plan_cannot_authorize_packing(self):
        rows = shared_bin_rows()
        rows["shared_bin_plans"][0]["valid_until"] = "2026-01-31T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "certificate expired at snapshot"):
            run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)

    def test_unassigned_sku_is_retried_after_another_sku_frees_a_bin(self):
        rows = shared_bin_rows()
        rows.pop("shared_bin_plans")
        rows.pop("shared_bin_plan_items")
        rows["locations"][0]["max_distinct_skus"] = "1"
        for location_id in ("L2", "L3"):
            location = rows["locations"][0].copy()
            location["location_id"] = location_id
            rows["locations"].append(location)
            node_id = "N" + location_id[1:]
            rows["aisle_nodes"].append(record("aisle_nodes", node_id=node_id, warehouse_id="WH1", node_type="location_access", floor_level="0", status="active"))
            rows["aisle_edges"].append(record("aisle_edges", edge_id="E" + location_id[1:], warehouse_id="WH1", from_node_id="N0", to_node_id=node_id, length_m="1" if location_id == "L3" else "5", bidirectional="true", travel_mode="picker", status="active"))
            rows["location_access"].append(record("location_access", access_id="AC" + location_id[1:], warehouse_id="WH1", location_id=location_id, node_id=node_id, walk_offset_m="1", travel_mode="picker", status="active"))
            rows["location_hazard_permissions"].append(record("location_hazard_permissions", permission_id="HP" + location_id[1:], warehouse_id="WH1", location_id=location_id, hazard_group="none"))
        rows["inventory_by_location"][1]["location_id"] = "L2"
        rows["slot_assignments"][1]["location_id"] = "L2"
        rows["location_pack_capacities"] = [
            record("location_pack_capacities", capacity_id="C1", warehouse_id="WH1", location_id="L2", packaging_profile_id="PK1", max_handling_unit_count="2", certified_at="2026-01-30T00:00:00Z", basis="site_test", approval_ref="fixture"),
            record("location_pack_capacities", capacity_id="C2", warehouse_id="WH1", location_id="L2", packaging_profile_id="PK2", max_handling_unit_count="2", certified_at="2026-01-30T00:00:00Z", basis="site_test", approval_ref="fixture"),
            record("location_pack_capacities", capacity_id="C3", warehouse_id="WH1", location_id="L3", packaging_profile_id="PK2", max_handling_unit_count="2", certified_at="2026-01-30T00:00:00Z", basis="site_test", approval_ref="fixture"),
        ]
        result = run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)
        decisions = {row["sku_id"]: row["proposed_location_id"] for row in result["recommendations"]}
        self.assertEqual(decisions, {"S1": "L2", "S2": "L3"})
        self.assertEqual(result["metrics"]["unassigned_skus"], 0)

    def test_manifestless_snapshot_mismatch_reports_both_exact_timestamps(self):
        rows = shared_bin_rows()
        rows["inventory_by_location"][0]["snapshot_at"] = "2026-01-31T11:59:30Z"
        with self.assertRaisesRegex(ValueError, r"inventory_by_location row 2: row snapshot_at=2026-01-31T11:59:30Z differs from entered snapshot_at=2026-01-31T12:00:00Z"):
            run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)

    def test_manifestless_post_snapshot_orders_are_processed_as_scenario_demand(self):
        rows = shared_bin_rows()
        rows.pop("pick_events")
        rows["orders"][0]["ordered_at"] = "2026-02-01T12:00:00Z"
        result = run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)
        self.assertEqual(result["metrics"]["assigned_skus"], 2)
        self.assertIn("post_snapshot_demand", {warning["code"] for warning in result["validation"]["warnings"]})
        self.assertTrue(any("post-snapshot demand window" in assumption for assumption in result["assumptions"]))

    def test_manifest_backed_upload_still_rejects_post_snapshot_orders(self):
        rows = shared_bin_rows()
        rows["orders"][0]["ordered_at"] = "2026-02-01T12:00:00Z"
        with self.assertRaisesRegex(ValueError, r"orders row 2: order at 2026-02-01T12:00:00Z is after snapshot_at=2026-01-31T12:00:00Z"):
            run_upload(self.files(rows, manifest=True), "WH1", SNAPSHOT)

    def test_manifestless_date_only_orders_after_snapshot_are_scenario_demand(self):
        rows = shared_bin_rows()
        rows.pop("pick_events")
        rows["orders"][0]["ordered_at"] = ""
        rows["orders"][0]["ordered_on"] = "2026-02-01"
        result = run_upload(self.files(rows, manifest=False), "WH1", SNAPSHOT)
        self.assertIn("post_snapshot_demand", {warning["code"] for warning in result["validation"]["warnings"]})

    def test_manifestless_future_snapshot_is_only_a_scenario_preview(self):
        from datetime import datetime, timedelta, timezone
        snapshot = datetime.now(timezone.utc) + timedelta(hours=2)
        snapshot_text = snapshot.isoformat().replace("+00:00", "Z")
        rows = shared_bin_rows()
        for table in ("inventory_by_location", "slot_assignments"):
            for row in rows[table]:
                row["snapshot_at"] = snapshot_text
        result = run_upload(self.files(rows, manifest=False), "WH1", snapshot_text)
        self.assertIn("future_snapshot_preview", {warning["code"] for warning in result["validation"]["warnings"]})
        self.assertTrue(any("scenario preview" in assumption for assumption in result["assumptions"]))

    def test_legacy_access_offset_upload_remains_supported(self):
        files = self.files(shared_bin_rows(), manifest=False)
        renamed = []
        for name, content in files:
            if name == "location_access.csv":
                content = content.replace(b"walk_offset_m", b"access_offset_m")
            renamed.append((name, content))
        result = run_upload(renamed, "WH1", SNAPSHOT)
        self.assertEqual(result["metrics"]["assigned_skus"], 2)

    def test_upload_rejects_both_offset_headers(self):
        files = self.files(shared_bin_rows(), manifest=False)
        amended = []
        for name, content in files:
            if name == "location_access.csv":
                lines = content.decode("utf-8").splitlines()
                lines[0] += ",access_offset_m"
                lines[1] += ",1"
                content = ("\n".join(lines) + "\n").encode("utf-8")
            amended.append((name, content))
        with self.assertRaisesRegex(ValueError, "duplicate columns for walk_offset_m"):
            run_upload(amended, "WH1", SNAPSHOT)

    def test_one_way_aisle_without_return_path_cannot_claim_round_trip(self):
        rows = shared_bin_rows()
        rows["aisle_edges"][0]["bidirectional"] = "false"
        result = run_upload(self.files(rows), "WH1", SNAPSHOT)
        self.assertEqual(result["statistics"]["candidate_locations"], 0)
        self.assertEqual(result["metrics"]["unassigned_skus"], 2)

    def test_route_does_not_shortcut_through_inactive_node(self):
        rows = shared_bin_rows()
        rows["aisle_nodes"].append(record("aisle_nodes", node_id="NX", warehouse_id="WH1", node_type="intersection", floor_level="0", status="inactive"))
        rows["aisle_edges"].extend([
            record("aisle_edges", edge_id="EX1", warehouse_id="WH1", from_node_id="N0", to_node_id="NX", length_m="1", bidirectional="true", travel_mode="picker", status="active"),
            record("aisle_edges", edge_id="EX2", warehouse_id="WH1", from_node_id="NX", to_node_id="N1", length_m="1", bidirectional="true", travel_mode="picker", status="active"),
        ])
        # The only valid path is the 5 m edge plus the 1 m walk offset, both ways.
        from backend.canonical_import import import_package
        write_package(self.folder, rows)
        imported = import_package(self.folder)
        self.assertTrue(imported.valid, imported.report)
        self.assertEqual(_paths(imported.tables, "WH1", "N0")["L1"], 12.0)

    def test_excel_workbook_sheets_are_imported(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl is not installed in this interpreter")
        book = Workbook()
        book.remove(book.active)
        for table, records in shared_bin_rows().items():
            sheet = book.create_sheet(table)
            from backend.canonical_schema import TABLES
            fields = [column.name for column in TABLES[table]]
            sheet.append(fields)
            for record_row in records:
                sheet.append([record_row.get(field, "") for field in fields])
        stream = io.BytesIO()
        book.save(stream)
        result = run_upload([("warehouse.xlsx", stream.getvalue())], "WH1", SNAPSHOT)
        self.assertEqual(result["metrics"]["assigned_skus"], 2)

    def test_sales_only_upload_explains_missing_physical_data(self):
        sales = Path(r"C:\Users\DurgaPrasad\Download\supply2")
        if not sales.exists():
            self.skipTest("Representative sales dataset is not available")
        files = [(name, (sales / name).read_bytes()) for name in ("product.csv", "sales_order.csv", "sales_order_details.csv")]
        with self.assertRaisesRegex(ValueError, "Missing physical warehouse tables"):
            run_upload(files, "Fixture warehouse", SNAPSHOT)

    def test_representative_sales_demand_with_synthetic_physical_fixture(self):
        source = Path(r"C:\Users\DurgaPrasad\Download\supply2")
        if not source.exists():
            self.skipTest("Representative sales dataset is not available")
        with (source / "sales_order_details.csv").open(newline="", encoding="utf-8-sig") as stream:
            lines = [row for row in csv.DictReader(stream) if row["line_status"] == "Closed"]
        most_common = [sku for sku, _ in Counter(row["product_id"] for row in lines).most_common(2)]
        with (source / "product.csv").open(newline="", encoding="utf-8-sig") as stream:
            names = {row["product_id"]: row["product_name"] for row in csv.DictReader(stream)}
        selected = [row for row in lines if row["product_id"] in most_common][:300]
        wanted_orders = {row["sales_order_id"] for row in selected}
        with (source / "sales_order.csv").open(newline="", encoding="utf-8-sig") as stream:
            sales_orders = [row for row in csv.DictReader(stream) if row["sales_order_id"] in wanted_orders and row["order_status"] == "Closed"]
        valid_orders = {row["sales_order_id"] for row in sales_orders}
        selected = [row for row in selected if row["sales_order_id"] in valid_orders]
        rows = shared_bin_rows()
        mapping = {"S1": most_common[0], "S2": most_common[1]}
        for table in ("skus", "sku_profiles", "sku_packaging_profiles", "sku_lots", "inventory_by_location", "slot_assignments"):
            for row in rows[table]:
                if row.get("sku_id") in mapping:
                    row["sku_id"] = mapping[row["sku_id"]]
                if table == "skus":
                    row["name"] = names[row["sku_id"]]
        rows["orders"] = [record("orders", order_id=row["sales_order_id"], warehouse_id="WH1", ordered_on=row["order_date"], status="fulfilled") for row in sales_orders]
        rows["order_lines"] = [record("order_lines", order_line_id=row["sales_order_detail_id"], order_id=row["sales_order_id"], sku_id=row["product_id"], ordered_qty=row["quantity_ordered"], status="fulfilled") for row in selected]
        rows.pop("pick_events")
        files = self.files(rows)
        manifest_path = self.folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["history_from"] = "2025-01-01T00:00:00Z"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        files = [(p.name, p.read_bytes()) for p in self.folder.iterdir()]
        result = run_upload(files, "WH1", SNAPSHOT)
        self.assertEqual(result["statistics"]["demand_lines"], len(selected))
        self.assertEqual(result["metrics"]["assigned_skus"], 2)
        self.assertEqual(result["metrics"]["unassigned_skus"], 0)
        print("Representative sales demand with synthetic physical fixture:", result["metrics"])

    def test_invalid_reference_does_not_call_engine(self):
        rows = shared_bin_rows()
        rows["inventory_by_location"][0]["location_id"] = "MISSING"
        with self.assertRaisesRegex(ValueError, "Canonical validation failed"):
            run_upload(self.files(rows), "WH1", SNAPSHOT)

    def test_fastapi_multipart_upload_and_legacy_demo(self):
        try:
            import httpx
            from backend.app import app
        except ImportError:
            self.skipTest("FastAPI/httpx are not installed in this interpreter")
        async def check():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                health = await asyncio.wait_for(client.get("/openapi.json"), timeout=5)
                self.assertEqual(health.status_code, 200)
                response = await asyncio.wait_for(client.post("/api/live/optimize", data={"warehouse_name": "WH1", "snapshot_at": SNAPSHOT},
                                             files=[("files", (name, content, "text/csv" if name.endswith(".csv") else "application/json")) for name, content in self.files(shared_bin_rows())]), timeout=10)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["metrics"]["assigned_skus"], 2)
                analysis_id = response.json()["analysis_id"]
                placements = await client.get(f"/api/live/export/{analysis_id}/placements.csv")
                self.assertEqual(placements.status_code, 200)
                self.assertIn("proposed_location_id", placements.text)
                self.assertIn("S1", placements.text)
                movements = await client.get(f"/api/live/export/{analysis_id}/movements.csv")
                self.assertEqual(movements.status_code, 200)
                self.assertIn("source_location_id", movements.text)
                workbook = await client.get(f"/api/live/export/{analysis_id}/results.xlsx")
                self.assertEqual(workbook.status_code, 200)
                from openpyxl import load_workbook
                book = load_workbook(io.BytesIO(workbook.content))
                self.assertEqual(book.sheetnames, ["Placements", "Stock movements"])
                self.assertEqual(book["Placements"]["C2"].value, "S1")
                legacy = await asyncio.wait_for(client.get("/api/overview"), timeout=10)
                self.assertEqual(legacy.status_code, 200, legacy.text)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
