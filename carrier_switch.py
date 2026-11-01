#!/usr/bin/env python3
"""T-Mobile carrier selector for iPhones on iOS 27 or later.

Uses original Apple-signed files for the connected model/build. No backup,
restore, downgrade or Find My operation. Physical results vary by release.
"""
import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import plistlib
import re
import ssl
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parent
RELEASE = "LightSpeedSwap 0.6.2"
TARGETS = {"mvno": "com.apple.TMobile_MVNO_US",
           "wholesale": "com.apple.TMobile_Wholesale_US"}
IMSI_KEY = "InternationalMobileSubscriberIdentity"
ICCID_KEY = "IntegratedCircuitCardIdentity"
INSTALL_TIMEOUT = 75
VERIFY_TIMEOUT = 30
REBOOT_TIMEOUT = 180
HARD_TIMEOUT = 420


class Stopped(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Stopped(message)


def say(message):
    print(message, flush=True)


def private_error(exc):
    # USB exceptions may contain plist dictionaries. Never print full SIM IDs.
    value = re.sub(r"[0-9]{12,}", "<SIM identifier redacted>", str(exc))
    value = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "<device identifier redacted>", value)
    return type(exc).__name__ + (": " + value[:1000] if value else "")


@dataclass(repr=False)
class State:
    product: str
    hardware: str
    version: str
    build: str
    rows: list = field(repr=False)
    imsi: str = field(default="", repr=False)
    iccid: str = field(default="", repr=False)
    gid1: str = field(default="", repr=False)

    selected_slot: str = ""

    @property
    def line(self):
        if self.selected_slot:
            matches = [r for r in self.rows if str(r.get("Slot", "")) == self.selected_slot]
            return matches[0] if len(matches) == 1 else None
        return self.rows[0] if len(self.rows) == 1 else None

    @property
    def current(self):
        return str(self.line.get("CFBundleIdentifier", "")) if self.line is not None else ""


def summarize(state):
    return {"tool_release": RELEASE, "product": state.product, "hardware": state.hardware,
            "version": state.version, "build": state.build,
            "carrier_rows": [{"bundle": row.get("CFBundleIdentifier", ""),
                              "version": row.get("CFBundleVersion", ""),
                              "mcc": row.get("MCC", ""), "mnc": row.get("MNC", ""),
                              "slot": row.get("Slot", "")} for row in state.rows],
            "imsi_available": bool(state.imsi), "iccid_available": bool(state.iccid),
            "gid1": state.gid1 or None,
            "selected_slot": state.selected_slot or None,
            "carrier_info_key_names": sorted({str(k) for row in state.rows for k in row})}


def validate_state(state):
    from carrier_payloads import identity
    try:
        identity(state)
    except ValueError as exc:
        raise Stopped(str(exc)) from exc
    require(state.line is not None,
            "Select exactly one reported line with --slot using its Slot value from status (for example kOne).")
    require(state.current in TARGETS.values(),
            "The active bundle is not TMobile_MVNO_US or TMobile_Wholesale_US.")
    require(re.fullmatch(r"[0-9]{14,15}", state.imsi) is not None,
            "The phone did not expose a usable IMSI over the paired USB connection. No install attempted.")
    require(state.imsi[:6] in {"310240", "310260"},
            "The reported IMSI is outside the analyzed T-Mobile selectors.")
    require(re.fullmatch(r"89[0-9]{16,20}", state.iccid) is not None,
            "The phone did not expose a usable ICCID over the paired USB connection. No install attempted.")
    for other in state.rows:
        if other is state.line:
            continue
        could_match = (other.get("CFBundleIdentifier") in TARGETS.values()
                       or (str(other.get("MCC", "")) == "310" and str(other.get("MNC", "")) in {"240", "260"}))
        if could_match:
            imsi, iccid, gid = str(other.get(IMSI_KEY, "")), str(other.get(ICCID_KEY, "")), other.get("GID1")
            require(imsi and iccid and gid and state.gid1,
                    "Another T-Mobile line has incomplete selector metadata. Disable that line before switching.")
            require(not (imsi.startswith(state.imsi) and iccid.startswith(state.iccid)
                         and normalize_gid1(gid).startswith(normalize_gid1(state.gid1))),
                    "Both lines match the same carrier selector. Disable the other T-Mobile line before switching.")


def normalize_gid1(value):
    if isinstance(value, bytes):
        value = value.hex()
    value = str(value or "").strip().removeprefix("0x").removeprefix("0X").upper()
    require(re.fullmatch(r"(?:[0-9A-F]{2}){1,8}", value) is not None,
            "GID1 must be a nonempty hexadecimal SIM group identifier (for example DD).")
    return value


def choose_gid1(state, supplied=None):
    detected = normalize_gid1(state.gid1) if state.gid1 else None
    if supplied is not None:
        chosen = normalize_gid1(supplied)
        require(not detected or detected == chosen,
                "Supplied GID1 does not match the phone's complete reported GID1. Shorter prefixes are not accepted.")
        return chosen
    require(detected is not None,
            "The paired phone did not expose GID1. Run diagnose; use --gid1 only with a confirmed SIM value. No install attempted.")
    return detected


def selector_name(state, gid1=None):
    validate_state(state)
    # Firmware parser: duplicate ID- fields use CFDictionaryAddValue, so the
    # first ICCID prefix remains. Empty GID1 NEVER matches CoreFoundation.
    # Full identities make the override stop matching ordinary replacement SIMs.
    # The native comparator is prefix-based; this is not a claim of file removal.
    # Full identifiers stay on the host/phone; never print or upload the package.
    return state.imsi + "_GID1-" + choose_gid1(state, gid1) + "_ID-" + state.iccid + "_ID-.bundle"


def legacy_selector_name(state, gid1=None):
    validate_state(state)
    for other in state.rows:
        if other is state.line:
            continue
        imsi, iccid = str(other.get(IMSI_KEY, '')), str(other.get(ICCID_KEY, ''))
        if imsi.startswith(state.imsi[:7]) and iccid.startswith(state.iccid[:9]):
            raise Stopped('Disable the other matching line before replacing a legacy broad selector.')
    return state.imsi[:7] + '_GID1-' + choose_gid1(state, gid1) + '_ID-' + state.iccid[:9] + '_ID-.bundle'


def load_files(target, state=None):
    from carrier_payloads import read_cached
    state = state or State("iPhone19,2", "V63AP", "27.2", "24B119", [])
    try:
        return read_cached(ROOT, state, target)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise Stopped(str(exc)) from exc


def make_package(state, target, gid1=None, legacy=False):
    name = legacy_selector_name(state, gid1) if legacy else selector_name(state, gid1)
    files = load_files(target, state)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # First entry must be Payload/NAME.bundle/, not Payload/: pymobiledevice3
        # uses this entry to recognize an IPCC. Explicit directories are needed
        # by its AFC upload path.
        root = "Payload/" + name + "/"
        archive.writestr(root, b"")
        archive.writestr(root + "signatures/", b"")
        for filename, data in sorted(files.items()):
            archive.writestr(root + filename, data)
    payload = output.getvalue()
    from pymobiledevice3.services.installation_proxy import classify_zip_file
    require(classify_zip_file(payload).is_ipcc(), "Host did not recognize the generated IPCC.")
    # Recheck actual ZIP bytes, not just the pre-packaging inputs.
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        recovered = {n[len(root):]: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
    require(recovered == files, "IPCC packaging changed a signed file.")
    return payload


class Phone:
    def __init__(self, udid=None, slot=None):
        self.udid, self.device, self.slot = udid, None, slot
        self.gid1_unavailable = False

    async def connect(self, autopair=True):
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.usbmux import list_devices
        devices = await asyncio.wait_for(list_devices(), 12)
        candidates = [d.serial for d in devices if d.connection_type == "USB"]
        if self.udid:
            candidates = [v for v in candidates if v.lower() == self.udid.lower()]
        require(len(candidates) == 1, "Connect one unlocked iPhone by USB and trust this Mac; use --udid if needed.")
        self.udid = candidates[0]
        self.device = await asyncio.wait_for(create_using_usbmux(
            serial=self.udid, connection_type="USB", autopair=autopair, pair_timeout=25), 35)

    async def close(self):
        if self.device is not None:
            device, self.device = self.device, None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(device.close(), 3)

    async def snapshot(self):
        from pymobiledevice3.exceptions import MissingValueError, PyMobileDevice3Exception
        values = await asyncio.wait_for(self.device.get_value(), 10)
        require(isinstance(values, dict), 'Unsupported device identity response. No install attempted.')
        try:
            rows = await asyncio.wait_for(self.device.get_value(key="CarrierBundleInfoArray"), 10)
        except MissingValueError:
            rows = []
        rows = rows or []
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
                "Unsupported carrier-info response.")
        ids = {}
        gid1 = ""
        chosen = [r for r in rows if str(r.get("Slot", "")) == self.slot] if self.slot else rows
        if len(chosen) == 1:
            row = chosen[0]
            # Root identifiers may belong to the other SIM. On multi-line
            # phones use only identities explicitly attached to the chosen row.
            fallback = values if len(rows) == 1 else {}
            for key in (IMSI_KEY, ICCID_KEY):
                ids[key] = str(row.get(key) or fallback.get(key) or "")
                if not ids[key] and len(rows) == 1:
                    try:
                        ids[key] = str(await asyncio.wait_for(self.device.get_value(key=key), 5) or "")
                    except MissingValueError:
                        pass
            reported_gid = row.get("GID1") or fallback.get("GID1")
            if not reported_gid and not self.gid1_unavailable and len(rows) == 1:
                try:
                    reported_gid = await asyncio.wait_for(self.device.get_value(key="GID1"), 5)
                except (PyMobileDevice3Exception, asyncio.TimeoutError):
                    # GID1 is optional read-only metadata, not available on
                    # every paired device. Do not delay every verification poll.
                    self.gid1_unavailable = True
            if reported_gid:
                gid1 = normalize_gid1(reported_gid)
        return State(str(values.get("ProductType", "")), str(values.get("HardwareModel", "")),
                     str(values.get("ProductVersion", "")), str(values.get("BuildVersion", "")),
                     rows, ids.get(IMSI_KEY, ""), ids.get(ICCID_KEY, ""), gid1, self.slot or "")

    async def install(self, payload):
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async with InstallationProxyService(self.device) as service:
            await service.install_from_bytes(payload)


    async def restart_and_reconnect(self, timeout=REBOOT_TIMEOUT):
        from pymobiledevice3.services.diagnostics import DiagnosticsService
        from pymobiledevice3.usbmux import list_devices
        # Restart takes effect when the diagnostics connection closes.
        async with DiagnosticsService(self.device) as service:
            await asyncio.wait_for(service.restart(), 15)
        await self.close()
        deadline = time.monotonic() + timeout
        disconnected = False
        while time.monotonic() < deadline:
            try:
                devices = await asyncio.wait_for(list_devices(), min(5, deadline - time.monotonic()))
                present = any(d.connection_type == "USB" and d.serial.lower() == self.udid.lower() for d in devices)
                if not present:
                    disconnected = True
                elif disconnected:
                    await asyncio.wait_for(self.connect(autopair=False), max(.01, min(10, deadline - time.monotonic())))
                    return True
            except Exception:
                await self.close()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(2, remaining))
        return False


async def observe_switch(phone, original, desired, timeout=VERIFY_TIMEOUT, interval=2, minimum_version=None):
    """Return only after the same SIM reports the requested bundle, or expire."""
    deadline = time.monotonic() + timeout
    last = "unavailable"
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            state = await asyncio.wait_for(phone.snapshot(), min(6, remaining))
            require((state.product, state.hardware.lower(), state.version, state.build) ==
                    (original.product, original.hardware.lower(), original.version, original.build),
                    "Device software or hardware changed during verification; switch unconfirmed.")
            if state.line is not None:
                last = state.current or "unavailable"
                if state.iccid and state.iccid != original.iccid:
                    raise Stopped("SIM identity changed during verification; activation is unconfirmed.")
                from carrier_payloads import version_tuple
                version_ok = minimum_version is None or version_tuple(str(state.line.get('CFBundleVersion', ''))) >= version_tuple(minimum_version)
                if state.current == desired and state.iccid == original.iccid and version_ok:
                    return True, last
        except Stopped:
            raise
        except Exception:
            # A transient disconnect may accompany a carrier reload. Observation
            # is bounded and never performs another installation or restart.
            pass
        if time.monotonic() < deadline:
            await asyncio.sleep(min(interval, deadline - time.monotonic()))
    return False, last


async def perform_switch(phone, state, target, reinstall=False,
                         install_timeout=INSTALL_TIMEOUT, verify_timeout=VERIFY_TIMEOUT, gid1=None,
                         no_reboot=False, reboot_timeout=REBOOT_TIMEOUT, legacy=False):
    validate_state(state)
    desired = TARGETS[target]
    say("Current: " + state.current + " -> Requested: " + desired)
    from carrier_payloads import version_tuple
    info = plistlib.loads(load_files(target, state)['Info.plist'])
    available = str(info['CFBundleVersion'])
    active = str(state.line.get('CFBundleVersion', ''))
    if state.current == desired and not reinstall and version_tuple(active) >= version_tuple(available):
        say("The requested bundle is already active at the same or a newer version. No install sent.")
        return 0
    require(version_tuple(available) >= version_tuple(active),
            'Refusing to downgrade carrier settings from ' + active + ' to ' + available
            + '. Import a current Apple-signed update pair using update-carriers.')
    chosen_gid = choose_gid1(state, gid1)
    payload = make_package(state, target, chosen_gid, legacy=legacy)
    say("GID1: " + chosen_gid + (" (reported by phone)" if state.gid1 else " (supplied; not verified from phone)"))
    say("Original signed files verified for this model/build; selection on other models remains experimental.")
    say("No backup or restore. Find My stays as it is. Cellular service may be interrupted.")
    say("After installation: " + ("manual reboot required (--no-reboot)." if no_reboot else "one restart; unlock the phone after it boots."))
    say("Sending one IPCC install (75-second limit)...")
    try:
        await asyncio.wait_for(phone.install(payload), install_timeout)
    except asyncio.TimeoutError as exc:
        raise Stopped("IPCC installation timed out; device-side completion is unknown. No retry sent. Run status before another switch.") from exc
    except Exception as exc:
        raise Stopped("IPCC installation did not complete: " + private_error(exc)
                      + ". No retry sent; run status to check the phone.") from exc
    if not no_reboot:
        say("Installer completed. Requesting one restart; unlock the same iPhone after it boots...")
        try:
            returned = await asyncio.wait_for(phone.restart_and_reconnect(reboot_timeout), reboot_timeout + 25)
        except Exception as exc:
            say("REBOOT UNCONFIRMED: " + private_error(exc))
            say("No second restart or install sent. Reboot manually if needed, then run status.")
            return 3
        if not returned:
            say("REBOOT UNCONFIRMED: the same USB device was not observed disconnecting and reconnecting within the limit.")
            say("Unlock/reconnect the phone, then run status. No second restart or install sent.")
            return 3
    say("Checking the same SIM's reported active bundle for up to " + str(verify_timeout) + " seconds...")
    switched, last = await observe_switch(phone, state, desired, timeout=verify_timeout, minimum_version=available)
    if not switched:
        say("UNCONFIRMED: installation completed, but the requested bundle was not observed active.")
        say("Last reported bundle: " + last)
        say("Reboot manually, then run status." if no_reboot else "Run status after unlocking the phone. No second install or restart sent.")
        return 3
    say("Verified: the same SIM now reports " + desired + " as its active bundle.")
    if no_reboot:
        say("REBOOT REQUIRED: the bundle is reported active, but carrier settings/indicators may stay stale until restart.")
        return 4
    say("Voice, data and IMS service still need an on-phone check.")
    return 0


def doctor():
    from importlib.metadata import version
    require((3, 11) <= sys.version_info[:2], "Run setup to prepare Python 3.11 or later.")
    require(ssl.OPENSSL_VERSION.startswith("OpenSSL "), "Use bash Setup.command for an OpenSSL Python runtime.")
    require(version("pymobiledevice3") == "11.49.1", "Run Setup.command for the tested pymobiledevice3 version.")
    require(version("cryptography") == "50.0.2", "Run setup for the tested cryptography version.")
    from cf_string_contract import check_native_macos_contract
    say("Selector " + RELEASE + "; " + check_native_macos_contract())
    from types import SimpleNamespace
    from carrier_payloads import load_bundled, validate_entry
    entries, objects = load_bundled(ROOT)
    for entry in entries.values():
        validate_entry(SimpleNamespace(**entry["device"]), entry, objects)
    say("Runtime checked; original Apple signatures verified for " + str(len(entries)) + " included model/build entries. No phone accessed.")
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version=RELEASE)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Offline dependency and signed-fixture checks")
    cache = sub.add_parser("cache", help="Offline cache size/contents or clear the downloaded bundle cache")
    cache.add_argument("action", choices=("info", "clear"), default="info", nargs="?")
    for command in ("status", "switch", "prepare", "diagnose", "setup-device", "export-pack", "revert", "migrate-legacy", "update-carriers"):
        c = sub.add_parser(command)
        c.add_argument("--udid", help="Select one USB device; never printed or saved")
        c.add_argument("--slot", help="Exact Slot value from status; required if more than one line is reported")
        if command == "setup-device":
            source = c.add_mutually_exclusive_group()
            source.add_argument("--firmware-url", help="Official Apple HTTPS IPSW URL for the phone's exact build")
            source.add_argument("--bundle-pack", type=Path, help="Import a previously exported pack for this exact model/build; no firmware download")
        if command == "export-pack":
            c.add_argument("--output", type=Path, required=True, help="Save the original bundle pair and firmware metadata as a small portable pack")
        if command == 'update-carriers':
            c.add_argument('--mvno-ipcc', type=Path, required=True)
            c.add_argument('--wholesale-ipcc', type=Path, required=True)
        if command == 'migrate-legacy':
            c.add_argument('--original', choices=sorted(TARGETS), required=True,
                           help='Known original bundle before an earlier prefix-selector release; replaces that one legacy payload')
        if command in ("switch", "prepare"):
            c.add_argument("--to", choices=sorted(TARGETS), required=True)
        if command in ("switch", "prepare", "revert", "migrate-legacy"):
            c.add_argument("--gid1", help="Confirmed SIM GID1 in hex; otherwise use a value reported by the phone")
            c.add_argument("--skip-backup", action="store_true", help="Accepted for compatibility; every command always skips backup")
        if command in ("switch", "revert", "migrate-legacy"):
            c.add_argument("--no-reboot", action="store_true", help="Leave the restart to you; a switch is not fully applied until reboot")
        if command == 'switch':
            c.add_argument("--reinstall", action="store_true", help="Install even if target is already reported active")
        if command == "prepare":
            c.add_argument("--output", type=Path, required=True, help="Write a device-derived IPCC locally, without installing")
        if command == "diagnose":
            c.add_argument("--seconds", type=int, choices=range(10, 61), metavar="10..60", default=45)
            c.add_argument("--output", type=Path, default=Path("carrier-diagnostic.json"))
    return p


async def run_phone(args):
    phone = Phone(args.udid, args.slot)
    try:
        await phone.connect()
        state = await phone.snapshot()
        if args.command in {"setup-device", "export-pack", "update-carriers"}:
            return state
        if args.command == "status":
            report = summarize(state)
            from carrier_payloads import locate
            try:
                for target in TARGETS:
                    load_files(target, state)
                report["payload_status"] = locate(ROOT, state)[0]
            except (Stopped, ValueError, KeyError, OSError) as exc:
                report["payload_status"] = "needs_setup_or_repair"
                report["payload_detail"] = str(exc)
            report["physical_test_coverage"] = "User confirmed both directions on iPhone19,2 / V63AP / 27.2 / 24B119 with reboot. Other models/builds are experimental."
            from carrier_hygiene import report as hygiene_report
            report['hygiene'] = hygiene_report(ROOT, phone.udid, state)
            report['carrier_update_policy'] = 'Exact-build payloads only; older carrier versions are never installed.'
            say(json.dumps(report, indent=2))
            return 0
        if args.command == "diagnose":
            from carrier_diagnostics import diagnose
            return await diagnose(phone, state, args.seconds, args.output)
        if args.command == "prepare":
            payload = make_package(state, args.to, args.gid1)
            with os.fdopen(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as f:
                f.write(payload)
            say("Prepared original signed files under the experimental selector directory: " + str(args.output))
            say("No install sent. This private package contains full SIM identifiers; do not share it.")
            return 0
        import carrier_hygiene as hygiene
        validate_state(state)
        baseline = None
        if args.command == 'revert':
            receipt = hygiene.current(ROOT, phone.udid, state)
            require(receipt is not None, 'No restoration receipt for this SIM. Its original bundle is unknown; no install sent.')
            target, reinstall = receipt['baseline'], True
        elif args.command == 'migrate-legacy':
            target, reinstall, baseline = args.original, True, args.original
            say('Replacing the earlier broad selector with the specified original bundle. This is not a clean uninstall.')
        else:
            target, reinstall = args.to, args.reinstall
        # Validate all package bytes before creating even the small host receipt.
        make_package(state, target, args.gid1, legacy=args.command == 'migrate-legacy')
        ident, _ = hygiene.begin(ROOT, phone.udid, state, target, baseline=baseline)
        try:
            result = await perform_switch(phone, state, target, reinstall, gid1=args.gid1,
                                          no_reboot=args.no_reboot, legacy=args.command == 'migrate-legacy')
        except Exception:
            hygiene.finish(ROOT, ident, 'unconfirmed')
            raise
        phase = ('baseline_restored' if args.command in {'revert', 'migrate-legacy'} else 'confirmed') if result == 0 else 'unconfirmed'
        hygiene.finish(ROOT, ident, phase)
        return result
    finally:
        await phone.close()


def main():
    args = parser().parse_args()
    logging.disable(logging.CRITICAL)
    if args.command == "cache":
        import carrier_store
        from carrier_payloads import cache_path
        if args.action == "clear":
            carrier_store.clear(cache_path(ROOT))
            say("Downloaded bundle cache cleared. Included payloads and the phone were not changed.")
        else:
            say(json.dumps(carrier_store.statistics(cache_path(ROOT)), indent=2))
        return 0
    if args.command == "doctor":
        return doctor()
    doctor()
    # Async cancellation can be held up by third-party service cleanup. The
    # independent deadline bounds the entire command, including that case.
    def expire():
        say("Stopped at the " + str(HARD_TIMEOUT) + "-second command limit. Device state may be unknown; no automatic retry. Run status before another switch.")
        os._exit(124)
    watchdog = threading.Timer(HARD_TIMEOUT, expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        result = asyncio.run(run_phone(args))
    finally:
        watchdog.cancel()
    if args.command == "setup-device":
        # The phone is already closed. Firmware preparation is host-only and
        # separately bounded by download, decrypt and mount timeouts.
        from carrier_payloads import setup_device
        setup_device(ROOT, result, args.firmware_url, args.bundle_pack)
        return 0
    if args.command == "export-pack":
        from carrier_payloads import export_pack
        count = export_pack(ROOT, result, args.output)
        say("Exported " + str(count) + " bytes to " + str(args.output) + ". No phone write or firmware download.")
        return 0
    if args.command == 'update-carriers':
        from carrier_payloads import import_carrier_updates
        import_carrier_updates(ROOT, result, args.mvno_ipcc, args.wholesale_ipcc)
        say('Verified and cached the newer signed pair. No phone write. Run switch to apply it.')
        return 0
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("Interrupted. No automatic retry; if installation had started, check status.")
        sys.exit(130)
    except Exception as exc:
        say("Stopped: " + (str(exc) if isinstance(exc, Stopped) else private_error(exc)))
        sys.exit(2)
