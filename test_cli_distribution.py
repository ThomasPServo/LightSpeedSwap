"""Build and execute the actual universal CLI; no USB or dependency downloads."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_cli
import carrier_paths
import zipapp_main

ROOT = Path(__file__).resolve().parent


class DistributionContracts(unittest.TestCase):
    def test_reproducible_archive_runs_help_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'LightSpeedSwap.pyz'
            first = build_cli.build(ROOT, path)
            self.assertEqual(build_cli.build(ROOT, path), first)
            for argument, expected in [('--help', 'setup-device'), ('--version', 'LightSpeedSwap 0.6.2')]:
                result = subprocess.run([sys.executable, str(path), argument], capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_archive_contains_only_the_cli_runtime_and_bundle_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'LightSpeedSwap.pyz'
            build_cli.build(ROOT, path)
            with zipfile.ZipFile(path) as z:
                self.assertEqual(set(z.namelist()), set(build_cli.RUNTIME_FILES) | {'__main__.py', 'app-index.json'})
            unpacked = Path(tmp) / 'unpacked'
            unpacked.mkdir()
            zipapp_main.unpack(path, unpacked)
            for name in build_cli.RUNTIME_FILES:
                self.assertEqual((unpacked / name).read_bytes(), (ROOT / name).read_bytes())

    def test_unpack_rejects_traversal_and_corruption(self):
        for name, digest in [('../outside.py', hashlib.sha256(b'x').hexdigest()), ('launcher.py', '0' * 64)]:
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'bad.pyz'
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr('__main__.py', b'')
                    z.writestr('app-index.json', json.dumps({name: digest}))
                    z.writestr(name, b'x')
                with self.assertRaises(ValueError):
                    zipapp_main.unpack(archive, tmp)

    def test_state_path_is_separate_from_ephemeral_application_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CARRIER_SELECTOR_HOME': tmp}):
            self.assertEqual(carrier_paths.data_root(Path('/temporary/app')), Path(tmp))
        with patch.dict(os.environ, {'CARRIER_SELECTOR_HOME': 'relative-path'}), self.assertRaises(ValueError):
            carrier_paths.data_root(ROOT)

    def test_unconfigured_app_does_not_install_or_contact_a_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'LightSpeedSwap.pyz'
            build_cli.build(ROOT, path)
            env = dict(os.environ, CARRIER_SELECTOR_HOME=str(Path(tmp) / 'state'))
            result = subprocess.run([sys.executable, str(path), 'switch', '--to', 'wholesale'],
                                    capture_output=True, text=True, timeout=30, env=env)
            self.assertEqual(result.returncode, 2)
            self.assertIn('setup', result.stderr)
            self.assertFalse((Path(tmp) / 'state').exists())


if __name__ == '__main__':
    unittest.main()
