"""Changed firmware and cache formats must stop before any device operation.

Future-version cases are synthetic parser checks, never physical compatibility
claims. Actual firmware payload checks live in test_firmware_matrix.py.
"""
import copy
import io
import json
import plistlib
import unittest
import zipfile

import carrier_payloads as p
import carrier_store as store
import carrier_switch as c
from test_carrier_payloads import manifest
from test_carrier_switch import state


def index_archive(index):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('index.json', index if isinstance(index, str) else json.dumps(index))
    return stream.getvalue()


class ChangedFormatContracts(unittest.TestCase):
    def test_older_and_future_version_labels_do_not_need_an_allowlist(self):
        for version, build in [('27.0', '24A437'), ('27.0.1', '24A900'),
                               ('27.12.3', '24Z900'), ('28.0', '25A1'), ('30.4', '27D42')]:
            s = state(version=version, build=build)
            self.assertEqual(p.validate_manifest(manifest(s), s), 'example.dmg.aea')

    def test_wrongly_typed_device_identity_is_a_clear_error(self):
        for name in ('product', 'hardware', 'version', 'build'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'identity fields'):
                p.identity(state(**{name: None}))

    def test_malformed_manifest_shapes_are_clear_errors(self):
        base = manifest(state())
        cases = [None, [], 'not a manifest']
        for field, value in [('SupportedProductTypes', 'iPhone19,2'),
                             ('BuildIdentities', None), ('BuildIdentities', [None]),
                             ('BuildIdentities', [{'Info': []}]),
                             ('BuildIdentities', [{'Info': {'DeviceClass': []}}])]:
            modified = copy.deepcopy(base)
            modified[field] = value
            cases.append(modified)
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Unsupported BuildManifest'):
                p.validate_manifest(value, state())

    def test_unknown_future_variant_does_not_fall_back_to_recovery(self):
        value = manifest(state(version='28.0', build='25A1'))
        value['BuildIdentities'][0]['Info']['Variant'] = 'Future Unknown Install'
        with self.assertRaisesRegex(ValueError, 'app update'):
            p.validate_manifest(value, state(version='28.0', build='25A1'))

    def test_additive_manifest_fields_remain_compatible(self):
        value = manifest(state())
        value['UnknownFutureMetadata'] = {'opaque': [1, 2]}
        value['BuildIdentities'][0]['Info']['AdditionalFlag'] = True
        self.assertEqual(p.validate_manifest(value, state()), 'example.dmg.aea')

    def test_changed_os_component_and_unsafe_path_rejected(self):
        for component in [None, [], {'Info': []}, {'Info': {'Path': ['image.dmg']}},
                          {'Info': {'Path': '../other.dmg'}}, {'Info': {'Path': '/image.dmg'}}]:
            value = manifest(state())
            value['BuildIdentities'][0]['Manifest']['OS'] = component
            with self.subTest(component=component), self.assertRaises(ValueError):
                p.validate_manifest(value, state())

    def test_unknown_signature_generation_is_not_silently_accepted(self):
        files = c.load_files('mvno')
        files['signatures/common.plist'] = plistlib.dumps({'CBSignature4': b'future-format'})
        with self.assertRaisesRegex(ValueError, 'signature format.*CBSignature3'):
            p.validate_files('mvno', files)

    def test_changed_signing_key_or_signature_cannot_pass(self):
        files = c.load_files('wholesale')
        signature = plistlib.loads(files['signatures/common.plist'])
        data = signature['CBSignature3']
        signature['CBSignature3'] = data[:-1] + bytes([data[-1] ^ 1])
        files['signatures/common.plist'] = plistlib.dumps(signature)
        with self.assertRaisesRegex(ValueError, 'signature failed'):
            p.validate_files('wholesale', files)

    def test_new_or_malformed_pack_schema_is_clear_error(self):
        for index in [[], None, {'schema': 2, 'entries': {}}, {'schema': True, 'entries': {}},
                      {'schema': 1, 'entries': []}]:
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'schema'):
                store.decode(index_archive(index))

    def test_duplicate_index_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate key'):
            store.decode(index_archive('{"schema":2,"schema":1,"entries":{}}'))

    def test_malformed_entry_metadata_is_clear_error(self):
        for entry in [None, [], {'device': []}, {'device': {'product': None}}]:
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'device metadata'):
                store.decode(index_archive({'schema': 1, 'entries': {'0' * 64: entry}}))


if __name__ == '__main__':
    unittest.main()
