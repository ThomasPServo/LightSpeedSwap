"""SIM replacement isolation, restoration receipts and carrier/software updates."""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

import carrier_hygiene as h
import carrier_payloads as p
import carrier_switch as c
from cf_string_contract import anchored_ascii_match
from test_carrier_switch import FakePhone, state


class HygieneContracts(unittest.TestCase):
    def test_same_prefix_replacement_no_longer_matches_full_identity(self):
        old = state()
        name = c.selector_name(old)
        self.assertIn(old.imsi, name)
        self.assertIn(old.iccid, name)
        for imsi, iccid in [(old.imsi, old.iccid[:-1] + '1'),
                            (old.imsi[:-1] + '0', old.iccid), ('', '')]:
            self.assertFalse(anchored_ascii_match(imsi, old.imsi) and anchored_ascii_match(iccid, old.iccid))

    def test_receipt_preserves_first_baseline_and_contains_no_plain_identifiers(self):
        st = state()
        with tempfile.TemporaryDirectory() as tmp:
            ident, first = h.begin(tmp, 'synthetic-device-id', st, 'wholesale')
            self.assertEqual(first['baseline'], 'mvno')
            h.finish(tmp, ident, 'confirmed')
            _, later = h.begin(tmp, 'synthetic-device-id', state('wholesale'), 'mvno')
            self.assertEqual(later['baseline'], 'mvno')
            raw = h.path(tmp).read_text()
            for private in (st.imsi, st.iccid, 'synthetic-device-id'):
                self.assertNotIn(private, raw)
            self.assertLess(len(raw), 1024)

    def test_new_sim_or_device_cannot_inherit_an_old_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            h.begin(tmp, 'phone-one', state(), 'wholesale')
            self.assertIsNone(h.current(tmp, 'phone-two', state()))
            self.assertIsNone(h.current(tmp, 'phone-one', state(iccid=state().iccid[:-1] + '1')))
            self.assertIsNone(h.current(tmp, 'phone-one', state(iccid='', imsi='')))

    def test_software_update_is_reported_and_never_reuses_old_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            h.begin(tmp, 'phone', state(), 'wholesale')
            updated = state(version='28.0', build='25A1')
            self.assertTrue(h.report(tmp, 'phone', updated)['software_changed_since_last_operation'])
            with self.assertRaises(p.MissingPayload):
                p.locate(c.ROOT, updated)

    def test_conflicting_legacy_baseline_does_not_overwrite_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            h.begin(tmp, 'phone', state(), 'wholesale')
            before = h.path(tmp).read_bytes()
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                h.begin(tmp, 'phone', state(), 'mvno', baseline='wholesale')
            self.assertEqual(h.path(tmp).read_bytes(), before)

    def test_imported_signed_update_pair_preserves_provenance_and_rejects_downgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(c.ROOT / 'bundled.carrierpack', root / 'bundled.carrierpack')
            paths = {}
            for target in c.TARGETS:
                # Original distribution-style IPCC, without a private selector.
                paths[target] = root / (target + '.ipcc')
                with zipfile.ZipFile(paths[target], 'w') as z:
                    for name, data in c.load_files(target).items():
                        z.writestr('Payload/' + p.NAMES[target] + '.bundle/' + name, data)
            p.import_carrier_updates(root, state(), paths['mvno'], paths['wholesale'])
            _, (entries, _), key = p.locate(root, state())
            self.assertIn('carrier update', entries[key]['origin'])
            original = p.cache_path(root).read_bytes()
            upgraded = state()
            upgraded.line['CFBundleVersion'] = '74.0'
            with self.assertRaisesRegex(ValueError, 'downgrade'):
                p.import_carrier_updates(root, upgraded, paths['mvno'], paths['wholesale'])
            self.assertEqual(p.cache_path(root).read_bytes(), original)


class UpdateInstallContracts(unittest.IsolatedAsyncioTestCase):
    async def test_newer_active_carrier_settings_are_never_downgraded(self):
        st = state()
        st.line['CFBundleVersion'] = '74.0'
        phone = FakePhone()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(c.Stopped, 'downgrade'):
            await c.perform_switch(phone, st, 'wholesale')
        self.assertEqual(phone.installs, [])
        self.assertEqual(phone.restarts, 0)

    async def test_same_bundle_identifier_refreshes_an_older_active_version(self):
        st = state()
        st.line['CFBundleVersion'] = '72.0'
        phone = FakePhone(observed=state())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await c.perform_switch(phone, st, 'mvno', verify_timeout=.1), 0)
        self.assertEqual(len(phone.installs), 1)

    async def test_stale_version_is_not_reported_as_success(self):
        st = state()
        st.line['CFBundleVersion'] = '72.0'
        phone = FakePhone(observed=st)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(await c.perform_switch(phone, st, 'mvno', verify_timeout=.01), 3)

    async def test_switch_then_revert_restores_recorded_baseline(self):
        class ConnectedFake(FakePhone):
            udid = 'synthetic-device'
            async def connect(self): pass
            async def close(self): pass
            async def snapshot(self):
                return self.observed if self.installs else self.initial
        root = c.ROOT
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'ROOT', Path(tmp)), \
             contextlib.redirect_stdout(io.StringIO()):
            shutil.copyfile(root / 'bundled.carrierpack', Path(tmp) / 'bundled.carrierpack')
            first = ConnectedFake(observed=state('wholesale'))
            first.initial = state()
            with patch.object(c, 'Phone', return_value=first):
                self.assertEqual(await c.run_phone(c.parser().parse_args(['switch', '--to', 'wholesale'])), 0)
            second = ConnectedFake(observed=state())
            second.initial = state('wholesale')
            with patch.object(c, 'Phone', return_value=second):
                self.assertEqual(await c.run_phone(c.parser().parse_args(['revert'])), 0)
            self.assertEqual(len(second.installs), 1)
            self.assertEqual(h.current(tmp, second.udid, state())['phase'], 'baseline_restored')


if __name__ == '__main__':
    unittest.main()
