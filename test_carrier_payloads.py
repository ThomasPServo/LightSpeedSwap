"""Offline model/build routing and importer failure boundaries."""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

import carrier_payloads as p
import carrier_switch as c
import carrier_store as store
from test_carrier_switch import state


def manifest(s):
    return {'ProductVersion': s.version, 'ProductBuildVersion': s.build,
            'SupportedProductTypes': [s.product],
            'BuildIdentities': [{'Ap,ProductType': s.product,
                                 'Info': {'DeviceClass': s.hardware.lower(), 'Variant': 'Customer Erase Install (IPSW)'},
                                 'Manifest': {'OS': {'Info': {'Path': 'example.dmg.aea'}}}}]}


class PayloadContracts(unittest.TestCase):
    def test_device_gate_accepts_all_iphone_generations_on_27_plus(self):
        # Only input eligibility is tested here, not model-specific activation.
        for product, board, version in [('iPhone12,1', 'N104AP', '27.0'),
                                        ('iPhone13,2', 'D53GAP', '27.1'),
                                        ('iPhone18,1', 'V53AP', '27.2'),
                                        ('iPhone19,4', 'V68AP', '27.2'),
                                        ('iPhone20,1', 'X99AP', '28.0')]:
            with self.subTest(product=product):
                s = state(product=product, hardware=board, version=version)
                c.validate_state(s)
                self.assertEqual(p.validate_manifest(manifest(s), s), 'example.dmg.aea')

    def test_rejects_noniphone_pre27_and_malformed_identifiers(self):
        for fields in ({'product': 'iPad17,1'}, {'version': '26.7'}, {'version': '27.bad'},
                       {'hardware': '../../x'}, {'build': '../build'}, {'product': 'iPhone../../'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                p.identity(state(**fields))

    def test_wrong_firmware_product_board_build_version_rejected(self):
        s = state()
        for fields in ({'product': 'iPhone12,1'}, {'hardware': 'N104AP'},
                       {'build': '24B115'}, {'version': '27.1'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                p.validate_manifest(manifest(state(**fields)), s)

    def test_manifest_with_ambiguous_image_rejected(self):
        s = state()
        value = manifest(s)
        value['BuildIdentities'].append({**value['BuildIdentities'][0],
            'Manifest': {'OS': {'Info': {'Path': 'other.dmg.aea'}}}})
        with self.assertRaises(ValueError):
            p.validate_manifest(value, s)

    def test_cache_roundtrip_exact_device_identity_and_signed_bytes(self):
        s = state()
        bundles = {t: c.load_files(t) for t in c.TARGETS}
        with tempfile.TemporaryDirectory() as tmp:
            destination = p.save_payload(tmp, s, plistlib.dumps(manifest(s)), 'offline-test', bundles)
            for target in c.TARGETS:
                self.assertEqual(p.read_cached(tmp, s, target), bundles[target])
            entries, objects = store.read(destination)
            receipt = entries[store.entry_key(p.identity(s))]
            self.assertNotIn(s.imsi, json.dumps(receipt))
            self.assertNotIn(s.iccid, json.dumps(receipt))
            # A changed archive member cannot be hidden by leaving its hash unchanged.
            import zipfile
            with zipfile.ZipFile(destination) as z:
                members = {n: z.read(n) for n in z.namelist()}
            key = receipt['bundles']['mvno']['files']['carrier.plist']
            members['objects/' + key] += b'changed'
            with zipfile.ZipFile(destination, 'w') as z:
                for name, data in members.items():
                    z.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'changed'):
                p.read_cached(tmp, s, 'mvno')

    def test_half_valid_pair_is_never_published(self):
        s = state()
        bundles = {t: c.load_files(t) for t in c.TARGETS}
        bundles['mvno'] = bundles['wholesale']
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'identity'):
                p.save_payload(tmp, s, plistlib.dumps(manifest(s)), 'offline-test', bundles)
            self.assertFalse(p.cache_path(tmp, s).exists())

    def test_unsupported_signature_layout_fails_before_publishing(self):
        bundles = {t: c.load_files(t) for t in c.TARGETS}
        bundles['mvno']['unrecognized.conf'] = b'extra'
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, 'Unmodeled'):
            p.save_payload(tmp, state(), plistlib.dumps(manifest(state())), 'offline-test', bundles)

    def test_renaming_override_without_signature_cannot_pass(self):
        files = c.load_files('mvno')
        files['overrides_N104.plist'] = files.pop('overrides_V64.plist')
        with self.assertRaises(ValueError):
            p.validate_files('mvno', files)

    def test_included_device_setup_does_not_access_network_or_firmware_reader(self):
        with patch.object(p, 'get_bytes', side_effect=AssertionError('network')), \
             patch.object(p, 'ensure_ipsw', side_effect=AssertionError('tool')):
            with contextlib.redirect_stdout(io.StringIO()):
                p.setup_device(c.ROOT, state())

    def test_firmware_index_sends_only_product_and_accepts_exact_build(self):
        s = state()
        data = {'firmwares': [{'buildid': '24B115', 'version': '27.2', 'url': 'https://updates.cdn-apple.com/old.ipsw'},
                              {'buildid': '24B119', 'version': '27.2', 'url': 'https://updates.cdn-apple.com/match.ipsw'}]}
        with patch.object(p, 'get_bytes', return_value=json.dumps(data).encode()) as request:
            self.assertEqual(p.firmware_url(s), data['firmwares'][1]['url'])
            url = request.call_args.args[0]
            self.assertIn(s.product, url)
            self.assertNotIn(s.imsi, url)
            self.assertNotIn(s.iccid, url)

    def test_nonapple_firmware_url_rejected(self):
        for url in ['http://updates.cdn-apple.com/a.ipsw', 'https://apple.com.evil.test/a.ipsw',
                    'file:///tmp/a.ipsw', 'https://evil.test/apple.com/a.ipsw']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                p.apple_url(url)

class ActualOlderFirmware(unittest.TestCase):
    def test_iphone11_original_group_and_both_packages_verify(self):
        from carrier_signature_audit import check_signatures
        s = state(product='iPhone12,1', hardware='N104AP')
        for target in c.TARGETS:
            files = c.load_files(target, s)
            groups = check_signatures(files)
            self.assertTrue(all(x['signature_valid'] for x in groups.values()))
            self.assertTrue(any('N104' in group for group in groups))
            self.assertNotIn('overrides_V64.plist', files)
            self.assertTrue(c.make_package(s, target))

    def test_wrong_group_signature_is_rejected(self):
        s = state(product='iPhone12,1', hardware='N104AP')
        files = c.load_files('mvno', s)
        filename = 'overrides_D421_D431_N104_D79.der.pri'
        files[filename] = files[filename] + b'changed'
        with self.assertRaisesRegex(ValueError, 'signature failed'):
            p.validate_files('mvno', files)


if __name__ == '__main__':
    unittest.main()
