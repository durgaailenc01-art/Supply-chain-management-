import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.benchmark_adapters import footwear_projection, slaprp_projection


class PublicAdapterTests(unittest.TestCase):
    def test_footwear_preserves_unknown_physical_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Product.csv").write_text("Reference;ABCCOD;Sector\nP1;A;S\n", encoding="utf-8")
            (root / "Storage_Location.csv").write_text("originalLocation,position,x,y,z\nB1,point,12,4,1\n", encoding="utf-8")
            (root / "Customer_Order.csv").write_text("orderNumber;Reference;quantity (units);creationDate\nO1;P1;3;19/10/2023 07:18\n", encoding="utf-8")
            mapped = footwear_projection(root)
            self.assertEqual(mapped["tables"]["orders"][0]["ordered_on"], "2023-10-19")
            self.assertEqual(mapped["tables"]["orders"][0]["ordered_at"], "")
            self.assertEqual(mapped["tables"]["locations"][0]["usable_volume_cm3"], "")
            self.assertEqual(mapped["tables"]["aisle_nodes"][0]["x_m"], "12")

    def test_slaprp_preserves_unknown_quantities_and_time(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "instances.zip"
            member = "instances/instances_Guo_2021/sample.txt"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(member, "2 3\n1 2 3\n3\n2\n2 1\n1 2\n3\n1 4\n")
            mapped = slaprp_projection(archive, member)
            self.assertEqual(mapped["counts"]["order_lines"], 3)
            self.assertEqual(mapped["tables"]["order_lines"][0]["ordered_qty"], "")
            self.assertEqual(mapped["tables"]["slot_assignments"][0]["location_id"], "4")
            self.assertEqual(mapped["tables"]["slot_assignments"][0]["snapshot_at"], "")
