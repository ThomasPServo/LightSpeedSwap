"""Compact-cache, portability, original signature and eviction contracts."""
import contextlib
import copy
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import carrier_payloads as p
import carrier_store as s
import carrier_switch as c
from test_carrier_switch import state
from test_carrier_payloads import manifest


def original_record():
    _, (entries, objects), key = p.locate(c.ROOT, state())
    return copy.deepcopy(entries[key]), dict(objects)


def fake_store_record(label, data=b'small object'):
    # Storage-only fixture: no claim of valid carrier files or firmware.
    device = {'product': label}
    key = s.digest(data)
    record = {'device': device, 'manifest': key, 'sequence': 0,
              'bundles': {t: {'files': {'fixture': key}} for t in ('mvno', 'wholesale')}}
    return record, {key: data}


class CompactStoreContracts(unittest.TestCase):
    def test_duplicate_payloads_are_stored_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.zip'
            for name in ('model1', 'model2', 'model3'):
                s.put(path, *fake_store_record(name))
            stats = s.statistics(path)
            self.assertEqual(stats['entries'], 3)
            self.assertEqual(stats['unique_objects'], 1)
            self.assertEqual(stats['duplicate_references_reused'], 2)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_ninth_identity_evicts_oldest_and_removes_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.zip'
            for n in range(9):
                result = s.put(path, *fake_store_record(str(n), ('value ' + str(n)).encode()))
            entries, objects = s.read(path)
            self.assertEqual(result['evicted_entries'], 1)
            self.assertEqual(len(entries), 8)
            self.assertNotIn(s.entry_key({'product': '0'}), entries)
            self.assertIn(s.entry_key({'product': '8'}), entries)
            self.assertNotIn(s.digest(b'value 0'), objects)

    def test_byte_budget_evicts_old_entries(self):
        import random
        with tempfile.TemporaryDirectory() as tmp, patch.object(s, 'MAX_CACHE_BYTES', 3000):
            path = Path(tmp) / 'cache.zip'
            data = random.Random(7).randbytes(1800)
            s.put(path, *fake_store_record('old', data))
            result = s.put(path, *fake_store_record('new', random.Random(8).randbytes(1800)))
            self.assertEqual(result['evicted_entries'], 1)
            self.assertLessEqual(path.stat().st_size, 3000)

    def test_oversized_new_pair_preserves_existing_cache(self):
        import random
        with tempfile.TemporaryDirectory() as tmp, patch.object(s, 'MAX_CACHE_BYTES', 3000):
            path = Path(tmp) / 'cache.zip'
            s.put(path, *fake_store_record('old'))
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'budget'):
                s.put(path, *fake_store_record('new', random.Random(3).randbytes(4000)))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix('.lock').exists())

    def test_failed_atomic_write_preserves_existing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.zip'
            s.put(path, *fake_store_record('old'))
            original = path.read_bytes()
            with patch.object(s, 'atomic_write', side_effect=OSError('disk full')), self.assertRaises(OSError):
                s.put(path, *fake_store_record('new'))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix('.lock').exists())

    def test_cache_clear_leaves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.zip'
            s.put(path, *fake_store_record('old'))
            unrelated = Path(tmp) / 'keep.txt'
            unrelated.write_text('keep')
            s.clear(path)
            self.assertFalse(path.exists())
            self.assertEqual(unrelated.read_text(), 'keep')

    def test_zip_unsafe_paths_rejected_without_extraction(self):
        for path in ('../outside', '/outside', 'objects/invalid'):
            with self.subTest(path=path):
                out = io.BytesIO()
                with zipfile.ZipFile(out, 'w') as z:
                    z.writestr(path, b'x')
                with self.assertRaisesRegex(ValueError, 'member path'):
                    s.decode(out.getvalue())


class PortablePackContracts(unittest.TestCase):
    def test_export_import_roundtrip_without_network_or_phone_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / 'phone.carrierpack'
            size = p.export_pack(c.ROOT, state(), pack)
            self.assertLess(size, 512 * 1024)
            with patch.object(p, 'get_bytes', side_effect=AssertionError('network')), \
                 patch.object(p, 'ensure_ipsw', side_effect=AssertionError('firmware tool')), \
                 contextlib.redirect_stdout(io.StringIO()):
                p.setup_device(tmp, state(), bundle_pack=pack)
            for target in c.TARGETS:
                self.assertEqual(p.read_cached(tmp, state(), target), c.load_files(target))
            entries, _ = s.read(pack)
            text = json.dumps(entries)
            self.assertNotIn(state().imsi, text)
            self.assertNotIn(state().iccid, text)

    def test_import_rejects_wrong_build_before_cache_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / 'phone.carrierpack'
            p.export_pack(c.ROOT, state(), pack)
            with self.assertRaisesRegex(ValueError, 'exact model'):
                p.import_pack(tmp, state(build='24B115'), pack)
            self.assertFalse(p.cache_path(tmp).exists())

    def test_signed_file_edit_cannot_be_hidden_by_recomputing_pack_hashes(self):
        record, objects = original_record()
        old = record['bundles']['mvno']['files']['carrier.plist']
        data = plistlib.loads(objects[old])
        data['CarrierName'] = 'Changed'
        data = plistlib.dumps(data)
        new = s.digest(data)
        record['bundles']['mvno']['files']['carrier.plist'] = new
        objects[new] = data
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / 'edited.carrierpack'
            pack.write_bytes(s.encode({s.entry_key(record['device']): record}, objects))
            with self.assertRaisesRegex(ValueError, 'signature failed'):
                p.import_pack(tmp, state(), pack)
            self.assertFalse(p.cache_path(tmp).exists())

    def test_foreign_hardware_override_groups_not_accepted(self):
        st = state(product='iPhone12,1', hardware='N104AP')
        bundles = {target: c.load_files(target) for target in c.TARGETS}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, 'hardware board'):
            p.save_payload(tmp, st, plistlib.dumps(manifest(st)), 'offline-test', bundles)

    def test_export_never_overwrites_user_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'keep.carrierpack'
            path.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                p.export_pack(c.ROOT, state(), path)
            self.assertEqual(path.read_bytes(), b'keep')

    def test_actual_manifest_selects_normal_os_instead_of_recovery_os(self):
        record, objects = original_record()
        m = plistlib.loads(objects[record['manifest']])
        paths = {i['Manifest']['OS']['Info']['Path'] for i in m['BuildIdentities'] if 'OS' in i['Manifest']}
        self.assertGreater(len(paths), 1)
        self.assertEqual(p.validate_manifest(m, state()), '141-68953-104.dmg.aea')

    def test_recovery_only_manifest_is_rejected(self):
        m = manifest(state())
        m['BuildIdentities'][0]['Info']['Variant'] = 'Recovery Customer Install'
        with self.assertRaisesRegex(ValueError, 'filesystem image'):
            p.validate_manifest(m, state())


if __name__ == '__main__':
    unittest.main()
