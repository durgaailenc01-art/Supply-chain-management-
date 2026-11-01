import unittest

from backend.schema_adapter import inspect_files, mapped_files
from backend.live_upload import _csv_rows, _canonicalize, run_upload
from backend.tests import test_live_workflow
from backend.tests.canonical_fixtures import shared_bin_rows, SNAPSHOT


class SchemaAdapterTests(unittest.TestCase):
    def test_unrelated_dataset_is_inspectable_without_fabricated_fields(self):
        result = inspect_files([('random.csv', b'Item;Sales;Comment\na;12;\nb;30;ok\n')])
        source = result['sources'][0]
        self.assertEqual(source['rows'], 2)
        self.assertEqual(source['blank_cells'], 1)
        self.assertEqual(source['table'], '')
        self.assertTrue(all(not target for target in source['mapping'].values()))
        self.assertIn('locations', result['required_tables'])

    def test_explicit_mapping_preserves_unknowns_and_values(self):
        files = [('random.csv', b'Code,Description\nX,Widget\n')]
        output = mapped_files(files, [{'source': 'random.csv', 'table': 'skus', 'mapping': {'Code': 'sku_id', 'Description': 'name'}}])
        self.assertEqual(_csv_rows(output[0][1], output[0][0]), [{'sku_id': 'X', 'name': 'Widget'}])
        with self.assertRaisesRegex(ValueError, 'missing required fields'):
            _canonicalize('skus', _csv_rows(output[0][1], output[0][0]))

    def test_duplicate_targets_and_manifest_rewriting_rejected(self):
        files = [('x.csv', b'A,B\n1,2\n')]
        with self.assertRaisesRegex(ValueError, 'duplicate mapped'):
            mapped_files(files, [{'source': 'x.csv', 'table': 'skus', 'mapping': {'A': 'sku_id', 'B': 'sku_id'}}])
        with self.assertRaisesRegex(ValueError, 'checksums'):
            mapped_files(files + [('manifest.json', b'{}')], [])

    def test_delimiters_and_utf16(self):
        for delimiter in [',', ';', '\t', '|']:
            text = f'ID{delimiter}Value\na{delimiter}12\nb{delimiter}13\n'
            self.assertEqual(_csv_rows(text.encode('utf-16'), 'x.csv')[0]['Value'], '12')

    def test_mapping_entire_canonical_fixture_executes_engine(self):
        helper = test_live_workflow.LiveWorkflowTests()
        helper.setUp()
        try:
            files = helper.files(shared_bin_rows(), manifest=False)
            renamed, specs = [], []
            for index, (name, content) in enumerate(files):
                table = name[:-4]
                rows = _csv_rows(content, name)
                new_name = f'export_{index}.csv'
                renamed.append((new_name, content))
                specs.append({'source': new_name, 'table': table, 'mapping': {key: key for key in rows[0]}})
            result = run_upload(mapped_files(renamed, specs), 'WH1', SNAPSHOT)
            self.assertGreater(result['metrics']['assigned_skus'], 0)
            self.assertEqual(result['metrics']['unassigned_skus'], 0)
        finally:
            helper.doCleanups()
