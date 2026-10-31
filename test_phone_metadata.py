"""No USB access: verify per-line identity and same-device reboot handling."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import carrier_switch as c
from test_carrier_switch import state


class MetadataContracts(unittest.IsolatedAsyncioTestCase):
    async def test_dual_sim_cannot_borrow_root_identity(self):
        s = state()
        values = {'ProductType': s.product, 'HardwareModel': s.hardware, 'ProductVersion': s.version,
                  'BuildVersion': s.build, c.IMSI_KEY: s.imsi, c.ICCID_KEY: s.iccid, 'GID1': 'DD'}
        rows = [{'Slot': 'kOne', 'CFBundleIdentifier': c.TARGETS['mvno']},
                {'Slot': 'kTwo', 'CFBundleIdentifier': 'com.apple.ATT_US'}]
        async def get_value(key=None):
            return values if key is None else rows
        phone = c.Phone(slot='kOne')
        phone.device = SimpleNamespace(get_value=AsyncMock(side_effect=get_value))
        result = await phone.snapshot()
        self.assertEqual(result.current, c.TARGETS['mvno'])
        self.assertEqual((result.imsi, result.iccid, result.gid1), ('', '', ''))
        with self.assertRaisesRegex(c.Stopped, 'usable IMSI'):
            c.validate_state(result)
        self.assertEqual(phone.device.get_value.await_count, 2)

    async def test_dual_sim_uses_only_selected_row_identity(self):
        s = state()
        rows = [{'Slot': 'kOne', 'CFBundleIdentifier': 'com.apple.ATT_US'},
                {'Slot': 'kTwo', 'CFBundleIdentifier': c.TARGETS['mvno'],
                 c.IMSI_KEY: s.imsi, c.ICCID_KEY: s.iccid, 'GID1': 'DD'}]
        values = {'ProductType': s.product, 'HardwareModel': s.hardware, 'ProductVersion': s.version, 'BuildVersion': s.build}
        async def get_value(key=None):
            return values if key is None else rows
        phone = c.Phone(slot='kTwo')
        phone.device = SimpleNamespace(get_value=AsyncMock(side_effect=get_value))
        result = await phone.snapshot()
        self.assertEqual((result.imsi, result.iccid, result.gid1), (s.imsi, s.iccid, 'DD'))
        c.validate_state(result)

    async def test_reboot_reconnects_only_original_usb_device_after_disconnect(self):
        phone = c.Phone(udid='original')
        phone.device = object()
        phone.close = AsyncMock()
        phone.connect = AsyncMock()
        service = SimpleNamespace(restart=AsyncMock())
        manager = AsyncMock()
        manager.__aenter__.return_value = service
        original = SimpleNamespace(serial='original', connection_type='USB')
        other = SimpleNamespace(serial='other', connection_type='USB')
        with patch('pymobiledevice3.services.diagnostics.DiagnosticsService', return_value=manager), \
             patch('pymobiledevice3.usbmux.list_devices', new=AsyncMock(side_effect=[[original], [other], [original, other]])), \
             patch('carrier_switch.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await phone.restart_and_reconnect(.5))
        service.restart.assert_awaited_once()
        manager.__aexit__.assert_awaited_once()
        phone.connect.assert_awaited_once_with(autopair=False)

    async def test_full_identity_separates_sims_with_the_same_old_prefixes(self):
        s = state()
        s.selected_slot = '1'
        s.rows.append({'Slot': '2', 'CFBundleIdentifier': c.TARGETS['mvno'],
                       c.IMSI_KEY: s.imsi[:-1] + '0', c.ICCID_KEY: s.iccid[:-1] + '1', 'GID1': s.gid1})
        c.validate_state(s)
        with self.assertRaisesRegex(c.Stopped, 'legacy broad selector'):
            c.legacy_selector_name(s)

    async def test_duplicate_full_identity_still_stops_before_install(self):
        s = state()
        s.selected_slot = '1'
        s.rows.append({'Slot': '2', 'CFBundleIdentifier': c.TARGETS['mvno'],
                       c.IMSI_KEY: s.imsi, c.ICCID_KEY: s.iccid, 'GID1': s.gid1})
        with self.assertRaisesRegex(c.Stopped, 'same carrier selector'):
            c.validate_state(s)
