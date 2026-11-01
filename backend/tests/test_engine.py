import unittest
from collections import Counter
from pathlib import Path

from backend.engine import assign, detect_exceptions, fits, metrics, read_csv, workload
from backend.validate import validate_dataset

DATASET = Path("data/starter")


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (DATASET / "manifest.json").exists():
            raise unittest.SkipTest("Generate starter dataset first")
        cls.profiles = read_csv(DATASET / "sku_profiles.csv")
        cls.locations = read_csv(DATASET / "locations.csv")
        cls.lines = read_csv(DATASET / "order_lines.csv")

    def test_integrity_and_five_expected_exceptions(self):
        result = validate_dataset(DATASET)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["exception_cases_correct"], 5)

    def test_global_assignment_respects_constraints(self):
        picks, _ = workload(self.lines)
        assigned = assign(self.profiles, self.locations, picks, baseline=False)
        location_ids = [a["location_id"] for a in assigned if a["location_id"]]
        self.assertEqual(len(location_ids), len(set(location_ids)))
        profile_map = {p["sku_id"]: p for p in self.profiles}
        loc_map = {l["location_id"]: l for l in self.locations}
        for assignment in assigned:
            self.assertTrue(assignment["location_id"])
            self.assertTrue(fits(profile_map[assignment["sku_id"]], loc_map[assignment["location_id"]]))

    def test_same_workload_and_improved_distance(self):
        picks, units = workload(self.lines)
        baseline = read_csv(DATASET / "slot_assignments.csv")
        optimized = assign(self.profiles, self.locations, picks, baseline=False)
        before = metrics(baseline, self.locations, self.profiles, picks, units)
        after = metrics(optimized, self.locations, self.profiles, picks, units)
        self.assertEqual(before["pick_lines"], after["pick_lines"])
        self.assertEqual(before["assigned_skus"], after["assigned_skus"])
        self.assertLess(after["picking_distance_m"], before["picking_distance_m"])


if __name__ == "__main__":
    unittest.main()
