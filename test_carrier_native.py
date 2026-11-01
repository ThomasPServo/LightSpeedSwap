"""Native archive boundaries and carrier extraction, without a phone or firmware download."""
import contextlib
import io
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import carrier_native as native
import carrier_payloads as payloads
import carrier_store as store

ROOT = Path(__file__).resolve().parent
STATE = SimpleNamespace(product='iPhone19,2', hardware='V63AP', version='27.2', build='24B119')
BASE = 'iOS/System/Library/Carrier Bundles/iPhone/'


def listing(bundles, prefix=BASE):
    return '\n\n'.join('Path = ' + prefix + payloads.NAMES[target] + '.bundle/' + name
                         + '\nMode = -rw-r--r--\nSize = ' + str(len(data))
                         for target, files in bundles.items() for name, data in files.items()) + '\n\n'


class NativeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entries, objects = payloads.load_bundled(ROOT)
        _, cls.bundles = payloads.validate_entry(STATE, entries[store.entry_key(payloads.identity(STATE))], objects)

    def test_selects_original_pair_with_or_without_volume_prefix(self):
        for prefix in (BASE, BASE.removeprefix('iOS/')):
            with self.subTest(prefix=prefix):
                chosen = native.select_members(listing(self.bundles, prefix), payloads.NAMES)
                self.assertEqual(len(chosen), sum(len(v) for v in self.bundles.values()))
                self.assertEqual({t for t, _, _ in chosen.values()}, {'mvno', 'wholesale'})

    def test_rejects_other_carriers_multiple_volumes_and_incomplete_pair(self):
        text = listing(self.bundles)
        cases = [text.replace('TMobile_MVNO_US', 'TMobile_US'),
                 text + text.replace('iOS/', 'OtherVolume/'),
                 listing({'mvno': self.bundles['mvno']}),
                 text.replace('iOS/System/Library/Carrier Bundles/iPhone/TMobile_Wholesale',
                              'Different/System/Library/Carrier Bundles/iPhone/TMobile_Wholesale')]
        for value in cases:
            with self.subTest(value=value[:60]), self.assertRaises(ValueError):
                native.select_members(value, payloads.NAMES)

    def test_rejects_paths_links_duplicate_fields_and_oversized_members(self):
        text = listing(self.bundles)
        for value in (text.replace('iOS/', '../'), text.replace('iOS/', 'C:/'),
                      text + text, text.replace('Mode = ', 'Symbolic Link = /tmp/other\nMode = ', 1),
                      text.replace('Mode = ', 'Hard Link = outside\nMode = ', 1),
                      text.replace('Mode = ', 'Mode = -rw-r--r--\nMode = ', 1),
                      text.replace('Mode = -', 'Mode = l', 1),
                      text.replace('Mode = -', 'Mode = b', 1),
                      text.replace('Mode = -rw-r--r--', 'Folder = -', 1),
                      text.replace('Mode = ', 'Folder = +\nMode = ', 1),
                      text.replace('Size = ', 'Size = 99999999', 1)):
            with self.subTest(value=value[:60]), self.assertRaises(ValueError):
                native.select_members(value, payloads.NAMES)
        for path in ('x/../carrier.plist', '/absolute', 'C:\\absolute', 'x/NUL.txt',
                     'x/Info.plist:stream', 'x/trailing.', 'x/line\nname'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                native.safe_path(path)

    def test_ignores_apfs_attributes_without_exporting_ntfs_streams(self):
        text = listing(self.bundles)
        attr = 'Path = ' + BASE + 'TMobile_MVNO_US.bundle/carrier.plist:com.apple.decmpfs\nAlternate Stream = +\nSize = 30\nFolder = -\n\n'
        self.assertEqual(native.select_members(text, payloads.NAMES),
                         native.select_members(text + attr, payloads.NAMES))

    def test_native_extraction_keeps_exact_bytes_and_rejects_extra_output(self):
        for extra in (False, True):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                image = work / 'firmware.dmg'
                image.write_bytes(b'\x00' * 32 + b'NXSB')
                def helper(args, **_):
                    if args[1] == 'l':
                        return listing(self.bundles).encode()
                    output = next(Path(str(v)[2:]) for v in args if str(v).startswith('-o'))
                    for target, files in self.bundles.items():
                        for name, data in files.items():
                            path = output / BASE / (payloads.NAMES[target] + '.bundle') / name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                            path.chmod(0o400)
                    if extra:
                        (output / 'unexpected').write_bytes(b'extra')
                    return b''
                with patch.object(native, 'run_capture', side_effect=helper), contextlib.redirect_stdout(io.StringIO()):
                    if extra:
                        with self.assertRaisesRegex(ValueError, 'unexpected'):
                            native.extract(image, work, '/fixture/7zz', payloads.NAMES)
                    else:
                        result = native.extract(image, work, '/fixture/7zz', payloads.NAMES)
                        self.assertEqual(result, self.bundles)
                        for target, files in result.items():
                            payloads.validate_files(target, files)
                self.assertTrue(all(p.stat().st_mode & stat.S_IWUSR for p in (work / 'extracted').rglob('*') if p.is_file()))

    def test_rejects_non_apfs_before_launching_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'image.dmg'
            image.write_bytes(b'unknown image')
            with patch.object(native, 'run_capture', side_effect=AssertionError('helper')):
                with self.assertRaisesRegex(ValueError, 'raw APFS'):
                    native.extract(image, Path(tmp), '/fixture/7zz', payloads.NAMES)

    def test_helper_cache_verifies_archives_and_cached_executables(self):
        executable = b'fixture, not executed'
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            item = tarfile.TarInfo('ipsw')
            item.size = len(executable)
            archive.addfile(item, io.BytesIO(executable))
        data = raw.getvalue()
        config = ('fixture.tar.gz', store.digest(data), {'ipsw': store.digest(executable)})
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CARRIER_SELECTOR_HOME': tmp}), \
             patch.object(native, 'host', return_value=('Linux', 'x86_64')), \
             patch.dict(native.IPSW, {('Linux', 'x86_64'): config}), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'archive checksum'):
                native.prepare_tool(tmp, lambda *_: b'wrong', 'ipsw')
            binary = native.prepare_tool(tmp, lambda *_: data, 'ipsw')
            self.assertEqual(binary.read_bytes(), executable)
            self.assertEqual(native.prepare_tool(tmp, lambda *_: self.fail('download'), 'ipsw'), binary)
            binary.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'Cached native helper checksum'):
                native.prepare_tool(tmp, lambda *_: self.fail('download'), 'ipsw')

    def test_host_aliases_select_correct_architecture_and_reject_unknown_hosts(self):
        for system in ('Windows', 'Linux'):
            for machine, arch in [('AMD64', 'x86_64'), ('x86_64', 'x86_64'), ('ARM64', 'arm64'), ('aarch64', 'arm64')]:
                with patch.object(native.platform, 'system', return_value=system), \
                     patch.object(native.platform, 'machine', return_value=machine):
                    self.assertEqual(native.host(), (system, arch))
        with patch.object(native.platform, 'system', return_value='Unknown'), self.assertRaises(ValueError):
            native.host()

    def test_native_process_timeout_and_output_limits_are_enforced(self):
        self.assertEqual(native.run_capture([sys.executable, '-c', 'print("ok")']).strip(), b'ok')
        with self.assertRaisesRegex(ValueError, 'timed out'):
            native.run_capture([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=0.1)
        with self.assertRaisesRegex(ValueError, 'output exceeded'):
            native.run_capture([sys.executable, '-c', 'print("x" * 100000)'], limit=1000)


if __name__ == '__main__':
    unittest.main()
