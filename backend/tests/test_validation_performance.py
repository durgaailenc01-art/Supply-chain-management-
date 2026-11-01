"""Check route-cache isolation and graph semantics without timing-sensitive tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.canonical_import import _reachable_nodes, import_package
from scripts.generate_large_slotwise_sample import generate


class ValidationPerformanceTests(unittest.TestCase):
    def test_graph_traversal_runs_once_per_warehouse_depot(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            generate(Path(directory), warehouses=2, locations=8, skus=4)
            with patch('backend.canonical_import._reachable_nodes', wraps=_reachable_nodes) as traversal:
                result = import_package(directory)
            self.assertTrue(result.valid, result.report['errors'])
            self.assertEqual(result.report['incidents'], [])
            self.assertEqual(traversal.call_count, 2)
            self.assertEqual(len(result.report['warehouses']['WH-01']['candidate_locations']), 4)
            self.assertEqual(len(result.report['warehouses']['WH-02']['candidate_locations']), 4)

    def test_reachability_preserves_direction_status_mode_and_warehouse(self):
        tables = {'aisle_nodes': [{'node_id': n, 'warehouse_id': w, 'status': s} for n, w, s in
                                 [('A', 'W', 'active'), ('B', 'W', 'active'), ('C', 'W', 'active'),
                                  ('D', 'W', 'inactive'), ('X', 'OTHER', 'active')]],
                  'aisle_edges': []}
        def edge(a, b, mode='picker', status='active', bidirectional=False):
            return {'warehouse_id': 'W', 'from_node_id': a, 'to_node_id': b,
                    'status': status, 'travel_mode': mode, 'bidirectional': bidirectional}
        tables['aisle_edges'] = [edge('A', 'B'), edge('B', 'C', mode='forklift'),
                                 edge('A', 'C', status='inactive'), edge('A', 'D'), edge('A', 'X')]
        self.assertEqual(_reachable_nodes(tables, 'W', 'A', 'picker'), {'A', 'B'})
        self.assertEqual(_reachable_nodes(tables, 'W', 'B', 'picker'), {'B'})
        self.assertEqual(_reachable_nodes(tables, 'W', 'D', 'picker'), set())
        tables['aisle_edges'][0]['bidirectional'] = True
        self.assertEqual(_reachable_nodes(tables, 'W', 'B', 'picker'), {'A', 'B'})
