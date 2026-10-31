"""Read-only diagnostic boundaries and SIM privacy; no USB is used."""
import asyncio
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import carrier_diagnostics as d
from test_carrier_switch import FakePhone, state


def event(message, pid=42):
    return SimpleNamespace(pid=pid, timestamp=datetime(2026, 10, 31, 12, 0),
                           level=SimpleNamespace(name="INFO"), image_name="/usr/libexec/CommCenter",
                           image_offset=123, message=message, label=None)


class FakeTrace:
    def __init__(self, events=(), names=None):
        self.events = events
        self.names = names if names is not None else {"42": {"ProcessName": "/usr/sbin/CommCenter"}}
        self.closed = False
        self.request = None

    async def get_pid_list(self):
        return {"Payload": self.names}

    async def close(self):
        self.closed = True

    async def syslog(self, **request):
        self.request = request
        for entry in self.events:
            yield entry
        await asyncio.Event().wait()


class DiagnosticContracts(unittest.IsolatedAsyncioTestCase):
    async def test_filters_pid_and_topic_and_redacts_identifiers(self):
        s = state()
        entries = [event("carrier bundle GID1=DD IMSI=" + s.imsi + " ICCID=" + s.iccid),
                   event("unrelated contact message"), event("carrier bundle other process", pid=99)]
        services = [FakeTrace(), FakeTrace(entries)]
        sequence = iter(services)
        phone = SimpleNamespace(device=object())
        result = await d.capture(phone, .01, (s.imsi, s.iccid), factory=lambda _: next(sequence))
        self.assertEqual(result["status"], "capture_window_ended")
        self.assertEqual(len(result["events"]), 1)
        self.assertNotIn(s.imsi, json.dumps(result))
        self.assertNotIn(s.iccid, json.dumps(result))
        self.assertIn("GID1=DD", result["events"][0]["message"])
        self.assertTrue(all(service.closed for service in services))
        from pymobiledevice3.services.os_trace import OsActivityStreamFlag as F
        self.assertEqual(services[1].request["pid"], 42)
        self.assertTrue(services[1].request["stream_flags"] & F.NO_SENSITIVE)

    async def test_missing_commcenter_never_falls_back_to_all_processes(self):
        service = FakeTrace(names={"9": {"ProcessName": "Other"}})
        result = await d.capture(SimpleNamespace(device=object()), .01, (), factory=lambda _: service)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(service.request)
        self.assertTrue(service.closed)

    async def test_trace_error_is_redacted_and_closed(self):
        service = FakeTrace()
        service.get_pid_list = AsyncMock(side_effect=RuntimeError(state().iccid))
        result = await d.capture(SimpleNamespace(device=object()), .01, (state().iccid,), factory=lambda _: service)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn(state().iccid, json.dumps(result))
        self.assertTrue(service.closed)

    async def test_event_limit_bounds_output(self):
        services = [FakeTrace(), FakeTrace([event("carrier bundle selected")] * 401)]
        sequence = iter(services)
        result = await d.capture(SimpleNamespace(device=object()), .1, (), factory=lambda _: next(sequence))
        self.assertEqual(result["status"], "capture_window_ended")
        self.assertEqual(result["discarded_older_events"], 1)
        self.assertEqual(len(result["events"]), d.MAX_EVENTS)

    async def test_diagnose_never_installs_or_reuses_output_path(self):
        phone = FakePhone(state(gid1=""))
        phone.udid = "private-test-device"
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            output = Path(tmp) / "diagnostic.json"
            result = {"status": "unavailable", "events": []}
            with patch.object(d, "capture", new=AsyncMock(return_value=result)) as capture:
                self.assertEqual(await d.diagnose(phone, state(gid1=""), 10, output), 0)
                saved = json.loads(output.read_text())
                self.assertEqual(saved["gid1_source"], "not_exposed_in_paired_metadata")
                self.assertEqual(phone.installs, [])
                with self.assertRaises(FileExistsError):
                    await d.diagnose(phone, state(), 10, output)
                self.assertEqual(capture.await_count, 1)
                self.assertEqual(phone.reads, 1)


if __name__ == "__main__":
    unittest.main()
