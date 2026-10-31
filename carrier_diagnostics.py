"""Bounded, read-only carrier metadata/log capture. No installation transport."""
import asyncio
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from carrier_switch import RELEASE, Stopped, require, say, summarize

MAX_EVENTS = 400
RELEVANT = re.compile(
    r"TMobile_(?:MVNO|Wholesale)|carrier[ _-]*bundle|SupportedSIMs|"
    r"\bGID1\b|_GID1-|matching.*(?:IMSI|ICCID)|Overlay active, not validating the SIMs",
    re.IGNORECASE,
)


def redact(text, secrets=()):
    text = str(text)
    for value in sorted({v for v in secrets if v}, key=len, reverse=True):
        text = text.replace(value, "<device/SIM identifier redacted>")
    text = re.sub(r"(?i)\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<UUID redacted>", text)
    text = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "<identifier redacted>", text)
    text = re.sub(r"\b[0-9]{10,}\b", "<number redacted>", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<email redacted>", text)
    text = re.sub(r"\+\d[\d ()-]{8,}\d", "<phone number redacted>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP redacted>", text)
    # Strip control characters from device strings before printing/saving.
    return "".join(c if c.isprintable() else " " for c in text)[:4000]


def event_record(entry, pid, secrets):
    label = getattr(entry, "label", None)
    category = str(getattr(label, "category", ""))
    routine = re.search(r"copyCarrierBundleValue|copyCarrierBundleValues|kCTClientXPCRequestCopyCarrierBundle", entry.message, re.I)
    if entry.pid != pid or routine or not (RELEVANT.search(entry.message) or category.startswith("cb.")):
        return None
    label = getattr(entry, "label", None)
    return {
        "timestamp": entry.timestamp.isoformat(),
        "level": str(getattr(entry.level, "name", entry.level)),
        "image": redact(Path(entry.image_name).name, secrets),
        "image_offset": int(entry.image_offset),
        "category": redact(getattr(label, "category", ""), secrets),
        "message": redact(entry.message, secrets),
    }


def service_factory(device):
    from pymobiledevice3.services.os_trace import OsTraceService
    return OsTraceService(device)


async def close_service(service):
    with contextlib.suppress(Exception):
        await asyncio.wait_for(service.close(), 2)


async def capture(phone, seconds, secrets, factory=service_factory):
    from pymobiledevice3.services.os_trace import OsActivityStreamFlag as F
    report = {"status": "unavailable", "historical_requested": True,
              "note": "Events can predate this command; absent or private logs prove nothing.",
              "events": [], "event_limit": MAX_EVENTS, "discarded_older_events": 0}
    lookup = factory(phone.device)
    try:
        response = await asyncio.wait_for(lookup.get_pid_list(), 8)
        pids = [int(pid) for pid, metadata in response.get("Payload", {}).items()
                if isinstance(metadata, dict)
                and Path(str(metadata.get("ProcessName", ""))).name == "CommCenter"]
        require(len(pids) == 1, "The read-only process list did not identify exactly one CommCenter.")
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + redact(str(exc), secrets)
        return report
    finally:
        await close_service(lookup)

    pid = pids[0]
    stream = factory(phone.device)
    flags = F.PROCESS_ONLY | F.PAYLOAD | F.HISTORICAL | F.DEBUG | F.NO_SENSITIVE | F.INFO

    async def collect():
        async for entry in stream.syslog(pid=pid, stream_flags=flags):
            record = event_record(entry, pid, secrets)
            if record is not None:
                report["events"].append(record)
                if len(report["events"]) > MAX_EVENTS:
                    report["events"].pop(0)
                    report["discarded_older_events"] += 1
        report["status"] = "stream_ended"

    try:
        await asyncio.wait_for(collect(), seconds)
    except asyncio.TimeoutError:
        report["status"] = "capture_window_ended"
    except Exception as exc:
        report["status"] = "stream_error"
        report["error"] = type(exc).__name__ + ": " + redact(str(exc), secrets)
    finally:
        await close_service(stream)
    return report


async def diagnose(phone, state, seconds, output):
    output = Path(output)
    require(10 <= seconds <= 60, "Diagnostic duration must be between 10 and 60 seconds.")
    # Exclusive creation fails before streaming if the chosen path exists.
    with output.open("x", encoding="utf-8") as destination:
        secrets = (state.imsi, state.iccid, phone.udid)
        report = {"tool_release": RELEASE, "operation": "read_only_diagnose",
                  "started_utc": datetime.now(timezone.utc).isoformat(),
                  "before": summarize(state),
                  "privacy": "Only carrier-related CommCenter messages are retained. Full known SIM/device identifiers and common identifier patterns are redacted. Review before sharing.",
                  "gid1_source": "paired_metadata" if state.gid1 else "not_exposed_in_paired_metadata"}
        say("Read-only diagnosis: no install, backup, restore, or restart.")
        say("Capturing carrier-related logs for up to " + str(seconds) + " seconds.")
        say("During capture, turn Airplane Mode on for 15 seconds, then off, to generate fresh selection logs.")
        report["trace"] = await capture(phone, seconds, secrets)
        try:
            after = await asyncio.wait_for(phone.snapshot(), 15)
            report["after"] = summarize(after)
            report["same_sim"] = bool(state.iccid and state.iccid == after.iccid)
        except Exception as exc:
            report["after_error"] = type(exc).__name__ + ": " + redact(str(exc), secrets)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["interpretation"] = (
            "GID1 from paired metadata can be used by switch automatically. Log values may be historical, redacted, or unrelated to the current SIM and are not used automatically. Installation and activation remain separate."
        )
        json.dump(report, destination, indent=2)
        destination.write("\n")
    say("Saved " + str(len(report["trace"]["events"])) + " filtered events to " + str(output))
    if state.gid1:
        say("The phone exposed GID1 " + state.gid1 + "; switch can use it automatically.")
    else:
        say("GID1 was not exposed in paired metadata. Review the diagnostic before another install; no value was guessed.")
    return 0
