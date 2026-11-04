"""Exact-build beta discovery and manifest gates, without a phone or downloads."""
import copy
import json
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

import carrier_payloads as p
import carrier_signature_audit as audit
import carrier_switch as c
from test_carrier_payloads import manifest
from test_carrier_switch import state


URL = ('https://updates.cdn-apple.com/2026FallSeed/7b4dad3a-c3aa-4e0f-9e1b-911d7b53867f/'
       'iPhone18,2_27.3_24C5037i_Restore.ipsw')
MISSING = json.dumps({'firmwares': []}).encode()


def beta_state():
    return state(product='iPhone18,2', hardware='V54AP', version='27.3', build='24C5037i')


def beta_record():
    # Reduced from littlebyteorg/appledb, osFiles/iOS/24x - 27.x/24C5037i.json.
    # This is discovery metadata, not a physical-device compatibility fixture.
    return {'osStr': 'iOS', 'version': '27.3 beta', 'build': '24C5037i',
            'sources': [{'type': 'ipsw', 'deviceMap': ['iPhone18,2'],
                         'links': [{'url': URL, 'active': True}]}]}


class FirmwareLookup(unittest.TestCase):
    def test_future_numeric_versions_and_models_need_no_new_allowlist(self):
        for version, build in [('28.0', '25A5001a'), ('30.12.3', '27Z5002b')]:
            st = state(product='iPhone99,2', hardware='X99AP', version=version, build=build)
            record = beta_record()
            record.update(version=version + ' beta 2', build=build)
            record['sources'][0]['deviceMap'] = [st.product]
            record['sources'][0]['boardMap'] = [st.hardware]
            with self.subTest(version=version), patch.object(p, 'get_bytes', side_effect=[MISSING, json.dumps(record).encode()]):
                self.assertEqual(p.firmware_url(st), URL)

    def lookup(self, record, primary=MISSING):
        with patch.object(p, 'get_bytes', side_effect=[primary, json.dumps(record).encode()]):
            return p.firmware_url(beta_state())

    def test_missing_beta_uses_exact_build_and_source_device_map(self):
        record = beta_record()
        record['sources'].extend([
            {'type': 'ipsw', 'deviceMap': ['iPhone19,2'], 'links': [{'url': URL + '?other', 'active': True}]},
            {'type': 'ota', 'deviceMap': ['iPhone18,2'], 'links': [{'url': URL + '?ota', 'active': True}]},
        ])
        self.assertEqual(self.lookup(record), URL)

    def test_index_outage_or_bad_response_can_use_independent_index(self):
        for primary in (URLError('offline'), HTTPError('https://api.ipsw.me/', 503, 'busy', {}, None),
                        b'not json', b'[]', b'{"firmwares":null}'):
            with self.subTest(primary=repr(primary)):
                self.assertEqual(self.lookup(beta_record(), primary), URL)

    def test_primary_exact_match_remains_one_request(self):
        data = {'identifier': 'iPhone18,2', 'firmwares': [
            {'buildid': '24C5037i', 'version': '27.3', 'url': URL}]}
        with patch.object(p, 'get_bytes', return_value=json.dumps(data).encode()) as fetch:
            self.assertEqual(p.firmware_url(beta_state()), URL)
            self.assertEqual(fetch.call_count, 1)

    def test_ambiguous_primary_index_does_not_guess_or_override(self):
        data = {'firmwares': [{'buildid': '24C5037i', 'version': '27.3', 'url': url}
                              for url in (URL, URL + '?different=1')]}
        with patch.object(p, 'get_bytes', return_value=json.dumps(data).encode()) as fetch:
            with self.assertRaisesRegex(ValueError, 'multiple exact-build'):
                p.firmware_url(beta_state())
            self.assertEqual(fetch.call_count, 1)

    def test_known_channel_labels_match_only_exact_numeric_version(self):
        for label in ('27.3', '27.3 beta', '27.3 beta 2', '27.3 RC', '27.3 RC 2'):
            with self.subTest(label=label):
                record = beta_record()
                record['version'] = label
                self.assertEqual(self.lookup(record), URL)
        for label in ('27.30 beta', '27.3.1 beta', '27.3 experimental', None):
            with self.subTest(label=label):
                record = beta_record()
                record['version'] = label
                with self.assertRaisesRegex(ValueError, 'exact iOS version/build'):
                    self.lookup(record)

    def test_other_build_os_device_and_board_cannot_be_selected(self):
        records = []
        for field, value in [('build', '24C5038i'), ('osStr', 'iPadOS')]:
            record = beta_record()
            record[field] = value
            records.append(record)
        for field, value in [('deviceMap', ['iPhone18,1']), ('boardMap', ['V53AP'])]:
            record = beta_record()
            record['sources'][0][field] = value
            records.append(record)
        for record in records:
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, '--firmware-url'):
                self.lookup(record)
        record = beta_record()
        record['sources'][0]['boardMap'] = ['V54AP']
        self.assertEqual(self.lookup(record), URL)

    def test_only_explicitly_active_links_and_one_distinct_url_are_accepted(self):
        for active in (False, None, 'true', 1):
            record = beta_record()
            record['sources'][0]['links'][0]['active'] = active
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, '0 matching'):
                self.lookup(record)
        record = beta_record()
        record['sources'] *= 2
        self.assertEqual(self.lookup(record), URL)
        record['sources'].append(copy.deepcopy(record['sources'][0]))
        record['sources'][-1]['links'][0]['url'] = URL + '?other=1'
        with self.assertRaisesRegex(ValueError, '2 matching'):
            self.lookup(record)

    def test_untrusted_download_urls_are_rejected(self):
        for url in ('http://updates.cdn-apple.com/a.ipsw', 'https://apple.com.evil.test/a.ipsw',
                    'https://updates.cdn-apple.com/a.zip', 'file:///tmp/a.ipsw',
                    'https://user@updates.cdn-apple.com/a.ipsw'):
            record = beta_record()
            record['sources'][0]['links'][0]['url'] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, '--firmware-url'):
                self.lookup(record)

    def test_published_api_http_mirror_does_not_make_https_match_ambiguous(self):
        record = beta_record()
        record['sources'][0]['links'][0]['preferred'] = True
        record['sources'][0]['links'].append({
            'url': URL.replace('https://updates.', 'http://updates-http.'),
            'active': True, 'preferred': False})
        self.assertEqual(self.lookup(record), URL)

    def test_malformed_metadata_has_actionable_error(self):
        for value in (None, [], {'osStr': 'iOS'}, {**beta_record(), 'sources': None},
                      {**beta_record(), 'sources': [None]}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '--firmware-url'):
                self.lookup(value)

    def test_both_indexes_unavailable_report_identity_and_recovery(self):
        with patch.object(p, 'get_bytes', side_effect=URLError('unavailable')):
            with self.assertRaises(ValueError) as caught:
                p.firmware_url(beta_state())
        for text in ('iPhone18,2', '27.3', '24C5037i', '--firmware-url', 'IPSW.me', 'AppleDB'):
            self.assertIn(text, str(caught.exception))

    def test_lookup_requests_have_only_public_identifiers_and_bounded_reads(self):
        s = beta_state()
        with patch.object(p, 'get_bytes', side_effect=[MISSING, json.dumps(beta_record()).encode()]) as fetch:
            self.assertEqual(p.firmware_url(s), URL)
        self.assertEqual(fetch.call_args_list[1].args,
                         ('https://api.appledb.dev/ios/iOS;24C5037i.json', 4 * 1024 * 1024))
        for call in fetch.call_args_list:
            self.assertNotIn(s.imsi, call.args[0])
            self.assertNotIn(s.iccid, call.args[0])

    def test_missing_firmware_stops_before_downloading_helpers_or_creating_cache(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(p, 'get_bytes', side_effect=[MISSING, json.dumps({**beta_record(), 'sources': []}).encode()]), \
             patch.object(p, 'ensure_firmware_tools', side_effect=AssertionError('helper download')):
            with self.assertRaisesRegex(ValueError, '--firmware-url'):
                p.setup_device(root, beta_state())
            self.assertFalse(p.cache_path(root).exists())


class BetaManifestContracts(unittest.TestCase):
    def test_developer_erase_and_upgrade_select_same_filesystem(self):
        s = beta_state()
        value = manifest(s)
        value['BuildIdentities'][0]['Info']['Variant'] = 'Developer Erase Install (IPSW)'
        upgrade = copy.deepcopy(value['BuildIdentities'][0])
        upgrade['Info']['Variant'] = 'Developer Upgrade Install (IPSW)'
        value['BuildIdentities'].append(upgrade)
        self.assertEqual(p.validate_manifest(value, s), 'example.dmg.aea')
        upgrade['Manifest']['OS']['Info']['Path'] = 'different.dmg.aea'
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            p.validate_manifest(value, s)

    def test_developer_manifest_still_requires_exact_product_board_version_build(self):
        s = beta_state()
        for field, different in [('product', 'iPhone19,2'), ('hardware', 'V53AP'),
                                 ('version', '27.2'), ('build', '24C5038i')]:
            other = copy.copy(s)
            setattr(other, field, different)
            value = manifest(other)
            value['BuildIdentities'][0]['Info']['Variant'] = 'Developer Erase Install (IPSW)'
            with self.subTest(field=field), self.assertRaises(ValueError):
                p.validate_manifest(value, s)

    def test_research_recovery_and_unknown_variants_remain_unsupported(self):
        for variant in ('Research Developer Erase Install (IPSW)', 'Recovery OS Install',
                        'Developer OTA', 'Unknown Install'):
            value = manifest(beta_state())
            value['BuildIdentities'][0]['Info']['Variant'] = variant
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, 'app update'):
                p.validate_manifest(value, beta_state())

    def test_self_consistent_nonapple_signing_key_is_never_trusted(self):
        # A key recovered from matching signatures is not an Apple trust anchor.
        # Sign every real payload group with our own key; all must still fail.
        files = c.load_files('wholesale')
        private = ec.generate_private_key(ec.SECP384R1())
        for group, result in audit.check_signatures(files).items():
            name = 'signatures/' + ('common' if group == 'common' else 'overrides_' + group) + '.plist'
            document = plistlib.loads(files[name])
            document['CBSignature3'] = private.sign(bytes.fromhex(result['sha384']),
                                                    ec.ECDSA(utils.Prehashed(hashes.SHA384())))
            files[name] = plistlib.dumps(document)
        self.assertTrue(all(not result['signature_valid'] for result in audit.check_signatures(files).values()))
        with self.assertRaisesRegex(ValueError, 'independently verified Apple key'):
            p.validate_files('wholesale', files)


if __name__ == '__main__':
    unittest.main()
