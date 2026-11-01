"""Protect manifest-backed sample bytes across Git checkouts and downloads."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from scripts.generate_large_slotwise_sample import generate
from backend.canonical_import import import_package

ROOT = Path(__file__).resolve().parents[2]


class SampleIntegrityTests(unittest.TestCase):
    def test_committed_sample_folder_and_zip_match_their_manifests(self):
        folder = ROOT / 'sample_data' / 'slotwise_20_warehouses'
        manifest = json.loads((folder / 'manifest.json').read_text())
        for name, metadata in manifest['files'].items():
            with self.subTest(source='folder', file=name):
                self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), metadata['sha256'])
        with zipfile.ZipFile(ROOT / 'sample_data' / 'slotwise_20_warehouses.zip') as archive:
            prefix = 'slotwise_20_warehouses/'
            archived = json.loads(archive.read(prefix + 'manifest.json'))
            self.assertEqual(archived, manifest)
            for name, metadata in archived['files'].items():
                with self.subTest(source='zip', file=name):
                    self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), metadata['sha256'])

    def test_windows_autocrlf_checkout_preserves_package_integrity(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            repo = Path(directory)
            folder = repo / 'sample_data' / 'slotwise_20_warehouses'
            generate(folder, warehouses=1, locations=4, skus=2)
            (repo / '.gitattributes').write_bytes((ROOT / '.gitattributes').read_bytes())

            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)

            git('init', '-q')
            git('config', 'core.autocrlf', 'true')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'sample')
            for path in folder.glob('*.csv'):
                path.unlink()
            git('checkout', '--', 'sample_data')
            result = import_package(folder)
            self.assertTrue(result.valid, result.report['errors'])
            self.assertFalse(any(b'\r\n' in path.read_bytes() for path in folder.glob('*.csv')))

    def test_modified_sample_still_fails_checksum(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            folder = Path(directory)
            generate(folder, warehouses=1, locations=4, skus=2)
            path = folder / 'locations.csv'
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
            result = import_package(folder)
            self.assertFalse(result.valid)
            self.assertTrue(any(error['code'] == 'checksum' for error in result.report['errors']))
