"""An MVNO/Wholesale operation must not target another carrier or SIM."""
import contextlib
import io
import unittest

import carrier_switch as c
from test_carrier_switch import FakePhone, state

OTHER_CARRIERS = [('com.apple.TMobile_US', '310260223456789', '8901260000000000001'),
                  ('com.apple.ATT_US', '310410123456789', '8901410000000000001'),
                  ('com.apple.Verizon_US', '311480123456789', '8914800000000000001')]


class CarrierIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_regular_tmobile_att_and_verizon_cannot_be_selected_for_either_target(self):
        for bundle, imsi, iccid in OTHER_CARRIERS:
            for target in c.TARGETS:
                st = state(imsi=imsi, iccid=iccid)
                st.line['CFBundleIdentifier'] = bundle
                phone = FakePhone()
                with self.subTest(bundle=bundle, target=target), contextlib.redirect_stdout(io.StringIO()), \
                     self.assertRaisesRegex(c.Stopped, 'active bundle'):
                    await c.perform_switch(phone, st, target)
                self.assertEqual(phone.installs, [])
                self.assertEqual(phone.restarts, 0)

    async def test_secondary_carrier_identities_never_enter_the_selected_package(self):
        for bundle, imsi, iccid in OTHER_CARRIERS:
            st = state()
            st.selected_slot = '1'
            st.rows.append({'Slot': '2', 'CFBundleIdentifier': bundle, c.IMSI_KEY: imsi,
                            c.ICCID_KEY: iccid, 'GID1': 'DD'})
            for target in c.TARGETS:
                with self.subTest(bundle=bundle, target=target):
                    name = c.selector_name(st)
                    self.assertIn(st.imsi, name)
                    self.assertIn(st.iccid, name)
                    self.assertNotIn(imsi, name)
                    self.assertNotIn(iccid, name)
                    c.make_package(st, target)

    async def test_legacy_migration_refuses_shared_tmobile_prefixes(self):
        st = state()
        st.selected_slot = '1'
        st.rows.append({'Slot': '2', 'CFBundleIdentifier': 'com.apple.TMobile_US',
                        c.IMSI_KEY: st.imsi[:-1] + '0', c.ICCID_KEY: st.iccid[:-1] + '1', 'GID1': 'DD'})
        with self.assertRaisesRegex(c.Stopped, 'legacy broad selector'):
            c.make_package(st, 'mvno', legacy=True)


if __name__ == '__main__':
    unittest.main()
