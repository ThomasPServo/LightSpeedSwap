"""Firmware-reader retention and temporary-image lifecycle; no Mac/USB needed."""
import contextlib
import io
from pathlib import Path
import plistlib
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import carrier_payloads as p
import carrier_store as s
import carrier_switch as c
from test_carrier_payloads import manifest
from test_carrier_switch import state


class PreparationStorageContracts(unittest.TestCase):
    def test_reader_retains_only_pinned_binary_and_rejects_corruption(self):
        executable = b'unit-test fixture, never executed'
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as tar:
            item = tarfile.TarInfo('ipsw')
            item.size = len(executable)
            tar.addfile(item, io.BytesIO(executable))
        data = raw.getvalue()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(p.platform, 'system', return_value='Darwin'), \
             patch.object(p.platform, 'machine', return_value='arm64'), \
             patch.dict(p.IPSW_DIGESTS, {'arm64': s.digest(data)}), \
             patch.dict(p.IPSW_BINARY_DIGESTS, {'arm64': s.digest(executable)}), \
             patch.object(p, 'get_bytes', return_value=data) as fetch, \
             contextlib.redirect_stdout(io.StringIO()):
            binary = p.ensure_ipsw(tmp)
            self.assertEqual(binary.read_bytes(), executable)
            self.assertEqual([v for v in Path(tmp).rglob('*') if v.is_file()], [binary])
            self.assertEqual(p.ensure_ipsw(tmp), binary)
            self.assertEqual(fetch.call_count, 1)
            binary.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                p.ensure_ipsw(tmp)
            self.assertEqual(fetch.call_count, 1)

    def run_preparation(self, tmp, cleanup_uncertain=False):
        st = state()
        manifest_bytes = plistlib.dumps(manifest(st))
        bundles = {t: c.load_files(t) for t in c.TARGETS}
        workdirs = []
        class FakeFirmware:
            def __init__(self, url):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def manifest(self):
                return manifest_bytes
            def download_image(self, name, path):
                workdirs.append(path.parent)
                path.write_bytes(b'encrypted fixture')
        def fake_command(args, **kwargs):
            args = [str(a) for a in args]
            if 'aea' in args:
                Path(args[3]).with_suffix('').write_bytes(b'decrypted fixture')
        def fake_extract(image, work):
            self.assertFalse(image.with_suffix('.dmg.aea').exists())
            if cleanup_uncertain:
                (work / 'mount-state-uncertain.txt').write_text('uncertain')
                raise ValueError('simulated mount cleanup failure')
            return bundles
        with patch.object(p.platform, 'system', return_value='Darwin'), \
             patch.object(p, 'ensure_ipsw', return_value=Path('/fake/ipsw')), \
             patch.object(p.shutil, 'disk_usage', return_value=SimpleNamespace(free=30 * 1024**3)), \
             patch('carrier_remote.RemoteFirmware', FakeFirmware), \
             patch.object(p, 'command', side_effect=fake_command), \
             patch.object(p, 'extract_macos', side_effect=fake_extract), \
             contextlib.redirect_stdout(io.StringIO()):
            if cleanup_uncertain:
                with self.assertRaisesRegex(ValueError, 'cleanup failure'):
                    p.setup_device(tmp, st, url='https://updates.cdn-apple.com/example.ipsw')
            else:
                p.setup_device(tmp, st, url='https://updates.cdn-apple.com/example.ipsw')
        return workdirs

    def test_success_removes_images_and_keeps_only_compressed_payload_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = self.run_preparation(tmp)
            self.assertTrue(all(not d.exists() for d in dirs))
            self.assertEqual([v for v in Path(tmp).rglob('*') if v.is_file()], [p.cache_path(tmp)])
            for t in c.TARGETS:
                self.assertEqual(p.read_cached(tmp, state(), t), c.load_files(t))

    def test_uncertain_mount_preserves_backing_image_and_creates_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = self.run_preparation(tmp, cleanup_uncertain=True)
            self.assertEqual(len(dirs), 1)
            self.assertTrue((dirs[0] / 'example.dmg').exists())
            self.assertFalse(p.cache_path(tmp).exists())


if __name__ == '__main__':
    unittest.main()
