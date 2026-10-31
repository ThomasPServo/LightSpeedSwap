"""Offline contract tests. No USB device is accessed by these tests."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import carrier_switch as c
from carrier_signature_audit import check_signatures


def state(target="mvno", **changes):
    values = dict(product="iPhone19,2", hardware="V63AP", version="27.2", build="24B119",
                  rows=[{"CFBundleIdentifier": c.TARGETS[target], "CFBundleVersion": "73.0", "Slot": "1"}],
                  imsi="310260123456789", iccid="8901260000000000000", gid1="DD")
    values.update(changes)
    return c.State(**values)


class FakePhone:
    def __init__(self, observed=None, failure=None, hang=False):
        self.observed = observed or state("wholesale")
        self.failure, self.hang = failure, hang
        self.installs, self.reads, self.restarts = [], 0, 0
        self.reboot_result, self.reboot_failure = True, None

    async def install(self, payload):
        self.installs.append(payload)
        if self.failure:
            raise self.failure
        if self.hang:
            await asyncio.Event().wait()

    async def restart_and_reconnect(self, timeout):
        self.restarts += 1
        if self.reboot_failure:
            raise self.reboot_failure
        return self.reboot_result

    async def snapshot(self):
        self.reads += 1
        return self.observed


class PackageContracts(unittest.TestCase):
    def test_original_signed_bytes_survive_both_packages(self):
        from pymobiledevice3.services.installation_proxy import classify_zip_file
        for target in c.TARGETS:
            original = c.load_files(target)
            payload = c.make_package(state(), target)
            self.assertTrue(classify_zip_file(payload).is_ipcc())
            with zipfile.ZipFile(io.BytesIO(payload)) as z:
                root = "Payload/" + c.selector_name(state()) + "/"
                self.assertEqual(z.namelist()[0], root)
                files = {n[len(root):]: z.read(n) for n in z.namelist() if not n.endswith("/")}
                self.assertEqual(files, original)
                self.assertIn(state().imsi, root)
                self.assertIn(state().iccid, root)
            self.assertTrue(all(x["signature_valid"] for x in check_signatures(files).values()))

    def test_switch_directions_use_same_install_directory(self):
        self.assertEqual(c.selector_name(state()), c.selector_name(state("wholesale")))

    def test_nonempty_actual_gid_and_actual_mnc_are_used(self):
        self.assertEqual(c.selector_name(state(imsi="310240123456789", gid1="4753")),
                         "310240123456789_GID1-4753_ID-8901260000000000000_ID-.bundle")

    def test_missing_invalid_or_conflicting_gid_rejected(self):
        for value in ("", "../DD", "D", "ZZ", "00" * 9):
            with self.subTest(value=value), self.assertRaises(c.Stopped):
                c.make_package(state(gid1=value), "wholesale")
        with self.assertRaisesRegex(c.Stopped, "does not match"):
            c.make_package(state(), "wholesale", "4753")

    def test_explicit_gid_supported_but_not_inferred(self):
        self.assertIn("_GID1-4753_", c.selector_name(state(gid1=""), "0x4753"))
        self.assertEqual(c.normalize_gid1(bytes.fromhex("ddff")), "DDFF")

    def test_supplied_gid_cannot_broaden_the_reported_selector(self):
        # A shorter supplied prefix could also match a second T-Mobile SIM.
        with self.assertRaisesRegex(c.Stopped, 'complete reported GID1'):
            c.make_package(state(gid1='DDFF'), 'wholesale', 'DD')

    def test_cf_empty_needle_regression(self):
        from cf_string_contract import CASES, anchored_ascii_match
        for haystack, needle, expected in CASES:
            with self.subTest(haystack=haystack, needle=needle):
                self.assertEqual(anchored_ascii_match(haystack, needle), expected)

    def test_missing_exact_payload_or_line_count_rejected(self):
        for change in [{"build": "24B115"}, {"version": "26.7"}, {"product": "iPhone18,2"},
                       {"hardware": "D94AP"}, {"rows": []}, {"rows": state().rows * 2}]:
            with self.subTest(change=change), self.assertRaises(c.Stopped):
                c.make_package(state(**change), "wholesale")

    def test_unavailable_or_unrelated_identifiers_never_guessed(self):
        for change in [{"imsi": ""}, {"iccid": ""}, {"imsi": "310410123456789"},
                       {"imsi": "3102601/../../"}, {"iccid": "garbage"}]:
            with self.subTest(change=change), self.assertRaises(c.Stopped):
                c.make_package(state(**change), "wholesale")

    def test_changed_signed_file_rejected(self):
        original_root = c.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            root = Path(tmp)
            shutil.copyfile(original_root / "bundled.carrierpack", root / "bundled.carrierpack")
            f = root / "bundled.carrierpack"
            f.write_bytes(f.read_bytes() + b"changed")
            with patch.object(c, "ROOT", root), self.assertRaises(c.Stopped):
                c.make_package(state(), "wholesale")

    def test_output_redacts_full_identifiers(self):
        s = state()
        output = json.dumps(c.summarize(s)) + repr(s) + c.private_error(RuntimeError(s.imsi + " " + s.iccid))
        self.assertNotIn(s.imsi, output)
        self.assertNotIn(s.iccid, output)

    def test_skip_backup_compatibility_option(self):
        args = c.parser().parse_args(["switch", "--to", "wholesale", "--skip-backup"])
        self.assertEqual(args.to, "wholesale")


class SwitchContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.stdout)
        self.redirect.__enter__()

    def tearDown(self):
        self.redirect.__exit__(None, None, None)

    async def test_verifies_new_bundle_on_same_sim(self):
        phone = FakePhone()
        result = await c.perform_switch(phone, state(), "wholesale", verify_timeout=.1)
        self.assertEqual(result, 0)
        self.assertEqual(len(phone.installs), 1)
        self.assertIn("Verified:", self.stdout.getvalue())
        self.assertEqual(phone.restarts, 1)

    async def test_reverse_direction_packages_mvno(self):
        phone = FakePhone(state("mvno"))
        result = await c.perform_switch(phone, state("wholesale"), "mvno", verify_timeout=.1)
        self.assertEqual(result, 0)
        import plistlib
        with zipfile.ZipFile(io.BytesIO(phone.installs[0])) as z:
            info = next(n for n in z.namelist() if n.endswith("/Info.plist"))
            self.assertEqual(plistlib.loads(z.read(info))["CFBundleIdentifier"], c.TARGETS["mvno"])

    async def test_accepted_install_is_not_activation_success(self):
        phone = FakePhone(state("mvno"))
        result = await c.perform_switch(phone, state(), "wholesale", verify_timeout=.01)
        self.assertEqual(result, 3)
        self.assertEqual(len(phone.installs), 1)
        self.assertNotIn("Verified:", self.stdout.getvalue())

    async def test_explicit_install_error_stops_without_followup(self):
        phone = FakePhone(failure=RuntimeError("Rejected"))
        with self.assertRaises(c.Stopped):
            await c.perform_switch(phone, state(), "wholesale", verify_timeout=.01)
        self.assertEqual(len(phone.installs), 1)
        self.assertEqual(phone.reads, 0)
        self.assertEqual(phone.restarts, 0)

    async def test_install_timeout_no_retry(self):
        phone = FakePhone(hang=True)
        with self.assertRaisesRegex(c.Stopped, "timed out"):
            await c.perform_switch(phone, state(), "wholesale", install_timeout=.01)
        self.assertEqual(len(phone.installs), 1)
        self.assertEqual(phone.reads, 0)
        self.assertEqual(phone.restarts, 0)

    async def test_sim_change_cannot_produce_success(self):
        phone = FakePhone(state("wholesale", iccid="8901260000000000001"))
        with self.assertRaisesRegex(c.Stopped, "SIM identity changed"):
            await c.perform_switch(phone, state(), "wholesale", verify_timeout=.1)

    async def test_missing_sim_id_cannot_produce_success(self):
        phone = FakePhone(state("wholesale", iccid=""))
        result = await c.perform_switch(phone, state(), "wholesale", verify_timeout=.01)
        self.assertEqual(result, 3)

    async def test_already_target_does_not_install(self):
        phone = FakePhone()
        self.assertEqual(await c.perform_switch(phone, state("wholesale", gid1=""), "wholesale"), 0)
        self.assertEqual(phone.installs, [])

    async def test_missing_gid_stops_before_install(self):
        phone = FakePhone()
        with self.assertRaisesRegex(c.Stopped, "did not expose GID1"):
            await c.perform_switch(phone, state(gid1=""), "wholesale")
        self.assertEqual(phone.installs, [])
        self.assertEqual(phone.reads, 0)
        self.assertEqual(phone.restarts, 0)

    async def test_reinstall_can_replace_unselected_candidate(self):
        phone = FakePhone(state("mvno"))
        self.assertEqual(await c.perform_switch(phone, state(), "mvno", reinstall=True, verify_timeout=.1), 0)
        self.assertEqual(len(phone.installs), 1)


    async def test_no_reboot_reports_pending_refresh(self):
        phone = FakePhone()
        self.assertEqual(await c.perform_switch(phone, state(), "wholesale", no_reboot=True, verify_timeout=.01), 4)
        self.assertEqual(phone.restarts, 0)
        self.assertIn("REBOOT REQUIRED", self.stdout.getvalue())

    async def test_failed_restart_does_not_reinstall_or_repeat_restart(self):
        phone = FakePhone()
        phone.reboot_failure = RuntimeError("Disconnected")
        self.assertEqual(await c.perform_switch(phone, state(), "wholesale"), 3)
        self.assertEqual(len(phone.installs), 1)
        self.assertEqual(phone.restarts, 1)
        self.assertNotIn("Verified:", self.stdout.getvalue())

    async def test_reboot_reconnect_timeout_is_unconfirmed(self):
        phone = FakePhone()
        phone.reboot_result = False
        self.assertEqual(await c.perform_switch(phone, state(), "wholesale"), 3)
        self.assertEqual(phone.reads, 0)
        self.assertNotIn("Verified:", self.stdout.getvalue())

    async def test_selected_line_is_verified_even_with_second_sim(self):
        initial = state()
        final = state("wholesale")
        other = {"CFBundleIdentifier": "com.apple.ATT_US", "Slot": "2"}
        initial.rows.append(other)
        final.rows = [other, *final.rows]
        initial.selected_slot = final.selected_slot = "1"
        phone = FakePhone(final)
        self.assertEqual(await c.perform_switch(phone, initial, "wholesale", verify_timeout=.01), 0)

    async def test_success_requires_same_build_after_reboot(self):
        phone = FakePhone(state("wholesale", build="24C99"))
        with self.assertRaisesRegex(c.Stopped, "Device software"):
            await c.perform_switch(phone, state(), "wholesale", verify_timeout=.01)


class DeadlineContract(unittest.TestCase):
    def test_process_deadline_even_when_async_cleanup_ignores_cancellation(self):
        code = '''
import asyncio,sys
import carrier_switch as c
c.HARD_TIMEOUT=.1
c.doctor=lambda:0
async def hung(args):
    while True:
        try: await asyncio.sleep(10)
        except asyncio.CancelledError: pass
c.run_phone=hung
sys.argv=['carrier_switch.py','status']
c.main()
'''
        p = subprocess.run([sys.executable, "-c", code], cwd=c.ROOT,
                           capture_output=True, text=True, timeout=3)
        self.assertEqual(p.returncode, 124)
        self.assertIn("Device state may be unknown", p.stdout)


if __name__ == "__main__":
    unittest.main()
