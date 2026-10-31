import unittest

try:
    from backend import app as api
except ImportError:
    api = None


@unittest.skipIf(api is None, "Install backend requirements to run API tests")
class ApiTests(unittest.TestCase):
    def test_core_handlers(self):
        self.assertGreater(api.overview()["metrics"]["distance_reduction_pct"], 0)
        self.assertTrue(api.warehouses())
        self.assertTrue(api.recommendations(None, "", "impact", 50, 0)["items"])
        self.assertEqual(api.overview("WH-001")["scope"]["warehouse_id"], "WH-001")
        self.assertEqual(len(api.exceptions()["items"]), 5)
        self.assertTrue(api.validation()["valid"])

    def test_optimization_post_creates_run(self):
        result = api.optimize()
        self.assertIn("run_id", result)
        self.assertEqual(result["optimized"]["unassigned_skus"], 0)

    def test_demand_forecast_reports_holdout(self):
        result = api.demand_forecast(10)
        self.assertEqual(result["holdout_days"], 14)
        self.assertGreaterEqual(result["holdout_mae"], 0)
        self.assertEqual(len(result["forecast"]), 10)

    def test_recommendation_search_and_warehouse_filter(self):
        result = api.recommendations("WH-001", "SKU-000007", "impact", 20, 0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["sku_id"], "SKU-000007")
        self.assertEqual(result["items"][0]["warehouse_id"], "WH-001")
        self.assertIn("distance_saving_m", result["items"][0])


if __name__ == "__main__":
    unittest.main()
