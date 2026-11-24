"""Update lifecycle contracts; synthetic versions are not future iOS support claims.

Version-policy fixtures use a private test trust anchor inside each test only.
They exercise the real signature verifier; production never trusts that key.
"""
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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

import carrier_payloads as p
import carrier_signature_audit as audit
import carrier_store as store
import carrier_switch as c
from test_carrier_payloads import manifest
from test_carrier_switch import FakePhone, state


class UpdateLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {t: c.load_files(t) for t in p.NAMES}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.st = state(version='30.4', build='27D42')
        self.key = ec.generate_private_key(ec.SECP384R1())
        public = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                   serialization.PublicFormat.UncompressedPoint)
        anchor = patch.object(audit, 'APPLE_PRODUCTION_P384', public)
        anchor.start()
        self.addCleanup(anchor.stop)

    def pair(self, mvno='74.0', wholesale='74.0', minimum=None):
        bundles = copy.deepcopy(self.original)
        for target, version in [('mvno', mvno), ('wholesale', wholesale)]:
            files = bundles[target]
            info = plistlib.loads(files['Info.plist'])
            info['CFBundleVersion'] = version
            if minimum is not None:
                info['MinimumOSVersion'] = minimum
            files['Info.plist'] = plistlib.dumps(info)
            for group, result in audit.check_signatures(files).items():
                name = 'signatures/' + ('common' if group == 'common' else 'overrides_' + group) + '.plist'
                signature = self.key.sign(bytes.fromhex(result['sha384']),
                                          ec.ECDSA(utils.Prehashed(hashes.SHA384())))
                files[name] = plistlib.dumps({'CBSignature3': signature})
            self.assertTrue(all(v['signature_valid'] for v in audit.check_signatures(files).values()))
        return bundles

    def save(self, bundles, st=None):
        st = st or self.st
        return p.save_payload(self.root, st, plistlib.dumps(manifest(st)),
                              'https://updates.cdn-apple.com/fixture.ipsw', bundles)

    def ipccs(self, bundles):
        paths = {}
        for target, files in bundles.items():
            paths[target] = self.root / (target + '.ipcc')
            with zipfile.ZipFile(paths[target], 'w') as archive:
                for name, data in files.items():
                    archive.writestr('Payload/' + p.NAMES[target] + '.bundle/' + name, data)
        return paths

    def test_old_export_cannot_replace_a_newer_cached_pair(self):
        self.save(self.pair('73.0', '73.0'))
        pack = self.root / 'old.carrierpack'
        p.export_pack(self.root, self.st, pack)
        self.save(self.pair('74.0', '74.0'))
        before = p.cache_path(self.root).read_bytes()
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            p.import_pack(self.root, self.st, pack)
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)
        self.assertFalse(p.cache_path(self.root).with_suffix('.lock').exists())

    def test_one_direction_cannot_be_downgraded_with_a_newer_other_direction(self):
        self.save(self.pair())
        before = p.cache_path(self.root).read_bytes()
        with self.assertRaisesRegex(ValueError, 'downgrade.*wholesale'):
            self.save(self.pair('75.0', '73.0'))
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)

    def test_included_baseline_also_prevents_a_pack_downgrade(self):
        self.save(self.pair('75.0', '75.0'))
        included = store.read(p.cache_path(self.root))
        store.clear(p.cache_path(self.root))
        (self.root / 'bundled.carrierpack').write_bytes(b'test-only included fixture')
        with patch.object(p, 'load_bundled', return_value=included), self.assertRaisesRegex(ValueError, 'downgrade'):
            self.save(self.pair())
        self.assertFalse(p.cache_path(self.root).exists())

    def test_newer_included_pair_is_not_shadowed_by_old_cache_after_app_update(self):
        self.save(self.pair())
        old_cache = p.cache_path(self.root).read_bytes()
        self.save(self.pair('75.0', '75.0'))
        included = store.read(p.cache_path(self.root))
        p.cache_path(self.root).write_bytes(old_cache)
        (self.root / 'bundled.carrierpack').write_bytes(b'test-only included fixture')
        with patch.object(p, 'load_bundled', return_value=included):
            report = p.update_status(self.root, self.st)
            self.assertEqual(report['source'], 'included')
            self.assertEqual(report['targets']['mvno']['prepared_version'], '75.0')
        self.assertEqual(p.cache_path(self.root).read_bytes(), old_cache)

    def test_newer_cached_pair_is_not_shadowed_by_an_app_update(self):
        self.save(self.pair())
        included = store.read(p.cache_path(self.root))
        self.save(self.pair('75.0', '75.0'))
        (self.root / 'bundled.carrierpack').write_bytes(b'test-only included fixture')
        with patch.object(p, 'load_bundled', return_value=included):
            report = p.update_status(self.root, self.st)
            self.assertEqual(report['source'], 'cached')
            self.assertEqual(report['targets']['mvno']['prepared_version'], '75.0')

    def test_conflicting_pairs_never_silently_regress_one_target(self):
        self.save(self.pair('74.0', '75.0'))
        included = store.read(p.cache_path(self.root))
        store.clear(p.cache_path(self.root))
        self.save(self.pair('75.0', '74.0'))
        before = p.cache_path(self.root).read_bytes()
        (self.root / 'bundled.carrierpack').write_bytes(b'test-only included fixture')
        with patch.object(p, 'load_bundled', return_value=included), self.assertRaisesRegex(ValueError, 'each contain a newer'):
            p.read_cached(self.root, self.st, 'mvno')
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)
        paths = self.ipccs(self.pair('76.0', '76.0'))
        with patch.object(p, 'load_bundled', return_value=included):
            p.import_carrier_updates(self.root, self.st, **paths)
            self.assertEqual(p.update_status(self.root, self.st)['targets']['wholesale']['prepared_version'], '76.0')

    def test_equivalent_numeric_versions_can_be_imported(self):
        self.save(self.pair())
        self.save(self.pair('74.0.0', '74.0.0.0'))
        self.assertEqual(p.update_status(self.root, self.st)['targets']['mvno']['prepared_version'], '74.0.0')

    def test_new_ios_build_keeps_its_own_versions_and_does_not_reuse_old_files(self):
        self.save(self.pair('75.0', '75.0'))
        other = state(version='31.0', build='28A42')
        with self.assertRaises(p.MissingPayload):
            p.read_cached(self.root, other, 'mvno')
        self.save(self.pair('74.0', '74.0'), other)
        self.assertEqual(p.update_status(self.root, self.st)['targets']['mvno']['prepared_version'], '75.0')
        self.assertEqual(p.update_status(self.root, other)['targets']['mvno']['prepared_version'], '74.0')

    def test_minimum_os_is_enforced_on_save_read_and_pack_import(self):
        self.save(self.pair())
        before = p.cache_path(self.root).read_bytes()
        bundles = self.pair(minimum='31.0')
        with self.assertRaisesRegex(ValueError, 'newer iOS'):
            self.save(bundles)
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)
        # Model a pack whose author assigned an incompatible firmware label.
        _, (entries, objects), key = p.locate(self.root, self.st)
        entry = entries[key]
        for target, files in bundles.items():
            entry['bundles'][target]['files'] = {n: store.digest(b) for n, b in files.items()}
            objects.update({store.digest(b): b for b in files.values()})
        pack = self.root / 'incompatible.carrierpack'
        pack.write_bytes(store.encode(entries, objects))
        with self.assertRaisesRegex(ValueError, 'newer iOS'):
            p.import_pack(self.root, self.st, pack)
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)
        p.cache_path(self.root).write_bytes(pack.read_bytes())
        with self.assertRaisesRegex(ValueError, 'newer iOS'):
            p.read_cached(self.root, self.st, 'mvno')

    def test_invalid_signed_minimum_os_and_bundle_versions_fail_closed(self):
        for bundles in (self.pair(minimum='future'), self.pair(minimum=31), self.pair(mvno=74)):
            with self.subTest(), self.assertRaisesRegex(ValueError, 'version format'):
                self.save(bundles)
            self.assertFalse(p.cache_path(self.root).exists())

    def test_pair_updates_compare_each_target_with_its_own_previous_version(self):
        self.save(self.pair('75.0', '74.0'))
        incoming = self.pair('76.0', '74.0')
        paths = self.ipccs(incoming)
        self.st.line['CFBundleVersion'] = '74.0'
        p.import_carrier_updates(self.root, self.st, **paths)
        report = p.update_status(self.root, self.st)
        self.assertEqual(report['targets']['mvno']['prepared_version'], '76.0')
        self.assertEqual(report['targets']['wholesale']['prepared_version'], '74.0')

    def refresh_fixture(self, incoming, during_download=None):
        st = self.st
        class Firmware:
            def __init__(self, url): pass
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def manifest(self): return plistlib.dumps(manifest(st))
            def download_image(self, name, destination, resume_dir=None):
                destination.write_bytes(b'fixture')
                if during_download:
                    during_download()
        def decrypt(args):
            Path(args[3]).with_suffix('').write_bytes(b'fixture')
        with patch('carrier_remote.RemoteFirmware', Firmware), \
             patch.object(p, 'firmware_url', return_value='https://updates.cdn-apple.com/test.ipsw'), \
             patch.object(p, 'ensure_firmware_tools', return_value='/test/ipsw'), \
             patch.object(p, 'command', side_effect=decrypt), \
             patch.object(p, 'extract_files', return_value=incoming), \
             contextlib.redirect_stdout(io.StringIO()):
            return p.setup_device(self.root, self.st, refresh=True)

    def test_refresh_updates_existing_cache_and_cleans_temporary_images(self):
        self.save(self.pair())
        self.refresh_fixture(self.pair('75.0', '75.0'))
        self.assertEqual(p.update_status(self.root, self.st)['targets']['mvno']['prepared_version'], '75.0')
        self.assertEqual(list(self.root.rglob('.firmware-*')), [])

    def test_refresh_cannot_replace_a_newer_carrier_only_update(self):
        self.save(self.pair('75.0', '75.0'))
        before = p.cache_path(self.root).read_bytes()
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            self.refresh_fixture(self.pair())
        self.assertEqual(p.cache_path(self.root).read_bytes(), before)
        self.assertEqual(list(self.root.rglob('.firmware-*')), [])

    def test_update_arriving_during_download_is_preserved(self):
        self.save(self.pair())
        newer = self.pair('76.0', '76.0')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            self.refresh_fixture(self.pair('75.0', '75.0'), lambda: self.save(newer))
        self.assertEqual(p.update_status(self.root, self.st)['targets']['mvno']['prepared_version'], '76.0')


class UpdateStatus(unittest.TestCase):
    def test_status_compares_versions_without_claiming_online_freshness(self):
        for active, expected in [('72.0', 'refresh_available'), ('73.0', 'matches_prepared_version'),
                                 ('73.0.0', 'matches_prepared_version'), ('74.0', 'newer_signed_pair_required'),
                                 ('future', 'unknown_active_version')]:
            st = state()
            st.line['CFBundleVersion'] = active
            with self.subTest(active=active):
                report = p.update_status(c.ROOT, st)
                self.assertEqual(report['targets']['mvno']['action'], expected)
                self.assertFalse(report['online_update_check'])
                self.assertIsNone(report['latest_available_version'])
                self.assertNotIn(st.imsi, json.dumps(report))
                self.assertNotIn(st.iccid, json.dumps(report))

    def test_missing_ambiguous_and_foreign_lines_are_not_ready_to_switch(self):
        for rows, expected in [([], 'select_one_line'), ([{}, {}], 'select_one_line'),
                               ([{'CFBundleIdentifier': 'com.apple.Verizon_US'}], 'unsupported_line')]:
            with self.subTest(rows=rows):
                report = p.update_status(c.ROOT, state(rows=rows))
                self.assertTrue(all(t['action'] == expected for t in report['targets'].values()))

    def test_cached_setup_warns_when_active_carrier_is_newer(self):
        st = state()
        st.line['CFBundleVersion'] = '74.0'
        output = io.StringIO()
        with patch.object(p, 'get_bytes', side_effect=AssertionError('network')), contextlib.redirect_stdout(output):
            p.setup_device(c.ROOT, st)
        self.assertIn('update-carriers', output.getvalue())
        self.assertNotIn('Ready to switch', output.getvalue())

    def test_explicit_url_is_checked_even_when_payload_is_included(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS Apple-hosted'):
            p.setup_device(c.ROOT, state(), url='https://untrusted.invalid/test.ipsw')

    def test_refresh_cannot_be_combined_with_pack_import(self):
        args = c.parser().parse_args(['setup-device', '--refresh', '--bundle-pack', 'example.carrierpack'])
        with self.assertRaisesRegex(ValueError, 'not both'):
            p.setup_device(c.ROOT, state(), bundle_pack=args.bundle_pack, refresh=args.refresh)


class StatusCLI(unittest.IsolatedAsyncioTestCase):
    async def test_status_includes_version_actions_without_device_writes(self):
        class ConnectedFake(FakePhone):
            udid = 'test-device'
            async def connect(self): pass
            async def close(self): pass
        phone = ConnectedFake(observed=state())
        output = io.StringIO()
        with patch.object(c, 'Phone', return_value=phone), contextlib.redirect_stdout(output):
            self.assertEqual(await c.run_phone(c.parser().parse_args(['status'])), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['carrier_updates']['targets']['mvno']['action'], 'matches_prepared_version')
        self.assertEqual(phone.installs, [])
        self.assertEqual(phone.restarts, 0)


if __name__ == '__main__':
    unittest.main()
