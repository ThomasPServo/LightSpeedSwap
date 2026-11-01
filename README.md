# LightSpeedSwap

A compact Python CLI for switching **TMobile_MVNO_US ↔ TMobile_Wholesale_US**
on an eligible iPhone running **iOS 27 or later**. It detects the active bundle,
uses original Apple-signed files for the exact firmware, installs once, restarts
once, and verifies the selected SIM afterward.

**No backup, restore, OS downgrade, or Find My change.** `--skip-backup` remains
accepted for compatibility; every command always skips backups.

Version 0.6.1 includes an experimental **radio dashboard** and
[native iPhone reader](ios/README.md). The [Ghidra analysis of 24B119](research/24B119-radio.md)
traces an on-device internet-QoS read path. Serving-cell reads have a separate
SPI permission requirement; usable QoS/cell measurements from the native reader
or USB collector have not been verified on a physical phone. Missing data stays unknown.

## Download and run

Download [LightSpeedSwap.pyz](https://github.com/ThomasPServo/LightSpeedSwap/raw/refs/heads/main/dist/LightSpeedSwap.pyz).
This is a universal **Python zipapp**, not a native standalone executable.
Python 3.9+ starts it; explicit one-time setup prepares a private Python 3.11+
runtime with the tested dependencies. Python and dependencies are not embedded
in the small app download. Check [SHA256SUMS.txt](dist/SHA256SUMS.txt) if desired.

On macOS/Linux:

```sh
python3 LightSpeedSwap.pyz setup
python3 LightSpeedSwap.pyz status
python3 LightSpeedSwap.pyz setup-device
python3 LightSpeedSwap.pyz switch --to wholesale
python3 LightSpeedSwap.pyz switch --to mvno
```

On Windows, use `py -3 LightSpeedSwap.pyz ...` with the same arguments. Windows
needs Apple's device drivers/services; Linux needs working usbmuxd. Automatic
firmware decryption/mounting requires macOS. Other hosts can import a trusted
pack for the exact model/build. USB behavior is not established by offline tests.

Unlock and trust the connected iPhone. Unlock it again after the restart.
`--no-reboot` leaves the restart to you and reports it as pending. If the target
bundle is already reported active, nothing is installed; use `--reinstall` only
when you explicitly want another installation.

From source, use `python3 launcher.py setup` and `python3 launcher.py status`.
The existing `bash Setup.command` and `bash Run.command ...` launchers also work.

## Radio dashboard and iPhone app

```sh
python3 LightSpeedSwap.pyz dashboard
python3 LightSpeedSwap.pyz dashboard --share
python3 LightSpeedSwap.pyz dashboard --import Saved-Radio.json
python3 LightSpeedSwap.pyz dashboard --demo
```

The default dashboard listens locally and reads current CommCenter log events
from one already trusted USB iPhone. It needs the prepared runtime; `--demo`,
`--offline`, and saved-file viewing work without setup or phone access. It does
not collect a backup or install a carrier bundle. It shows QCI/5QI only when a
QoS object contains the class, and bands only from explicit cell measurements.
Logging may omit or redact these fields. Demo readings are always synthetic;
saved reports are always historical.

**Streaming means logs are arriving, not that QoS or bands are available.**
The dashboard counts QoS/cell measurements separately from mode events. An empty
stream of measurements displays **NO MEASUREMENTS**. Expand **Reader diagnostics**
for counts of candidate messages, undecoded candidates, redaction markers and
unattributed modes; these counters are included in exported JSON without raw
logs. A candidate only contains a recognized field name and may not contain a
measurement. Older exports cannot retrospectively supply these counters.

`--share` enables the same dashboard on the trusted local network using a
temporary access key. It prints a phone link for Safari or the native app.
Sharing is HTTP, so use a trusted network. The key is kept out of URL query
strings, access logs and exports. Exports omit raw log messages and subscriber
identifiers. Saved JSON/JSONL and extracted macOS `.logarchive` / log-bb captures
can be imported; raw logarchive decoding requires macOS's `log` command.

The [native app](ios/README.md) has **Read this iPhone**, foreground polling,
per-SIM views, JSON export/import and an optional Mac connection. Build and sign
it in Xcode, or personally sign the unsigned artifact from
[Native iOS reader CI](https://github.com/ThomasPServo/LightSpeedSwap/actions/workflows/ios.yml).
No private Apple entitlements are included. Its QoS access is experimental;
stock-iOS connected-band access remains unresolved. The app never estimates
priority from a carrier bundle, speed test, signal strength or flow ID.

## Exact firmware without a growing collection of files

There is no fixed phone-model or firmware-build allowlist. `setup-device` checks
the connected product, hardware board, iOS version, and build, then retrieves
that exact firmware's carrier pair. An absent beta/RC index entry can use:

```sh
python3 LightSpeedSwap.pyz setup-device --firmware-url '<official Apple HTTPS IPSW URL>'
```

The manifest must match all four identity fields. The normal Customer IPSW OS
image is selected; Recovery and unknown install variants are not substituted.
Original carrier signatures and applicable board overrides must verify.
Signed files are never edited to make a package pass verification.

Prepared payloads occupy **one compressed, deduplicated cache**, capped at
**8 MiB and eight exact model/build entries**. An entry contains both directions.
The oldest prepared entries are evicted automatically when necessary. Oversized
new entries or failed writes leave the previous cache intact. Unknown cache
schemas stop with an explicit update requirement.

The included seven-model/build pack is 362,992 bytes: 39 unique objects replace
116 file references. The complete CLI is under 500 KiB.

```sh
python3 LightSpeedSwap.pyz cache info
python3 LightSpeedSwap.pyz cache clear
python3 LightSpeedSwap.pyz export-pack --output MyPhone.carrierpack
python3 LightSpeedSwap.pyz setup-device --bundle-pack MyPhone.carrierpack
```

Exported packs contain original signed files and public firmware metadata,
not SIM identifiers or a backup. Imports need no firmware download. Only import
packs you prepared or trust: carrier signatures authenticate the carrier bytes,
not an arbitrary pack author's firmware labels. Export never overwrites a file.

The `.pyz` stores its private runtime and cache under:

| Host | Default state directory |
| --- | --- |
| macOS | `~/Library/Application Support/LightSpeedSwap` |
| Linux | `$XDG_DATA_HOME/LightSpeedSwap`, or `~/.local/share/LightSpeedSwap` |
| Windows | `%LOCALAPPDATA%\LightSpeedSwap` |

`CARRIER_SELECTOR_HOME` can specify an absolute alternative. The source launcher
defaults to its source directory. Zipapp code is unpacked into a temporary folder
for each run and removed afterward; the runtime and bounded cache are shared
across app updates. A crashed cache writer can leave a lock; close app processes
before removing a stale lock reported by the app.

**Temporary space is different from retained cache size.** An unfamiliar firmware
still requires several GB of download and at least 25 GiB free temporary space;
larger images require more. HTTP ranges stream only the selected IPSW member,
using bounded reads and checking its complete ZIP CRC. This is not sparse
extraction of individual files from an encrypted APFS image. After successful
decryption/extraction/unmounting, the large images are deleted. Uncertain mount
cleanup preserves the backing image and reports its location.

The optional pinned `ipsw` decryption helper retains one executable (about
83 MiB on Apple Silicon or 88 MiB on Intel). Its archive and executable hashes
are checked. Runtime/dependencies, the helper, and manually exported packs are
outside the payload cache cap. No full firmware image is shipped in this repo.

## Compatibility and evidence

See [VALIDATION.md](VALIDATION.md) for actual firmware and host test coverage.
The user's physical confirmation covers both directions on
**iPhone19,2 / V63AP / iOS 27.2 / 24B119**, with reboot needed to refresh remaining
settings. Other phone/build combinations are experimental until physically tested.

Future versions with the supported formats can be prepared without changing a
model/build list. Changed signature keys, unknown signing formats, missing carrier
files, ambiguous OS images, or unusable USB metadata stop clearly. Synthetic
future-version tests establish parser behavior only. They cannot guarantee that
a future iOS release will retain this selector/install mechanism.

The selected line must already report MVNO or Wholesale, an IMSI beginning
`310240` or `310260`, a usable ICCID, and a nonempty GID1. `--gid1` accepts a
confirmed value when unavailable; a supplied value cannot shorten a reported
GID1 and broaden its match. No carrier name or log guess supplies missing IDs.

With multiple lines, select the exact `Slot` value from status using `--slot`.
Identifiers must belong to that line; root metadata is never borrowed from a
different line. If two T-Mobile lines match the same selector, temporarily
disable the other line yourself. The app does not disable SIMs.

Regular **T-Mobile (`TMobile_US`), AT&T, and Verizon are rejected as selected
targets**. Packages contain only the chosen MVNO/Wholesale bundle and a selector
derived from that line's full identity. Other line identities never enter that
selector. Offline isolation checks do not establish physical dual-SIM behavior.

## eSIM hygiene and returning to the original bundle

New selectors use the **full IMSI, full ICCID, and GID1**. Removal or ordinary
replacement of the eSIM makes that selector stop matching, including a replacement
with the same old seven/nine-digit prefixes. This behavior was checked against
24B119 parser/scoring machine-code excerpts; it is not a physical replacement test.
The native comparison remains prefix-based, not a new exact-equality API. Reusing
the same full SIM identities can reactivate an installed selector.

Before an installation, the app records the original bundle name in a small
private host receipt. It stores keyed fingerprints of the device/SIM identity,
not the identifiers themselves or a copy of phone settings. Failed or interrupted
operations keep their original baseline and are marked unconfirmed. To return
the same SIM to its recorded original bundle using currently verified files:

```sh
python3 LightSpeedSwap.pyz revert
```

A different SIM cannot inherit that receipt. `status` reports the baseline and
whether software changed since the last operation. Keep the state directory if
you want `revert` to remember it. Clearing the payload cache preserves receipts.

**Upgrading from the earlier prefix-selector app:** that existing selector does
not disappear merely because this release uses a narrower one. For the original
MVNO baseline in the earlier experiment, run once on the same SIM:

```sh
python3 LightSpeedSwap.pyz migrate-legacy --original mvno
python3 LightSpeedSwap.pyz switch --to wholesale
```

Migration replaces that known broad selector's payload with the specified
original bundle, then restarts and verifies. It refuses ambiguous simultaneous
T-Mobile lines. Do not supply an original bundle you do not know. This command
has not been physically tested, and does not remove other historical selectors.

**This is functional isolation and baseline restoration, not a clean uninstall.**
Installed files and normal carrier aliases can remain. The reviewed paired-USB
service exposes carrier installation, not a validated carrier-removal operation.
A CLI cannot delete files or react while closed/disconnected. Automatic return to
the exact pre-run filesystem state after deleting an eSIM is therefore not claimed.

## iOS and carrier updates

After an iOS update, run `setup-device` again. Payload identity includes the exact
new version/build, so an old firmware cache is never silently reused. Then run
`switch` for the desired target. Even when its bundle identifier is already active,
a newer prepared carrier version is installed and verified by version as well as
SIM identity. A newer active carrier version is never replaced with older files.

If Apple ships a carrier-only update newer than the firmware's bundled files,
prepare the exact firmware first, then import both original signed update IPCCs:

```sh
python3 LightSpeedSwap.pyz update-carriers --mvno-ipcc MVNO.ipcc --wholesale-ipcc Wholesale.ipcc
python3 LightSpeedSwap.pyz switch --to wholesale
```

Both identities, signatures, board overrides, versions, and a declared minimum
OS version are checked. An invalid/incomplete/downgrade pair leaves the cache
untouched. Importing updates is host-only; it does not install them. An exported
pack preserves the carrier-update provenance.

This does not promise automatic Apple OTA behavior: an installed custom selector
can outrank later broad carrier candidates. Reconnect and refresh after updates.
There is no background updater, no invented download URL for unavailable carrier
IPCCs, and no guarantee of compatibility with an unreleased iOS selector mechanism.

One install has a 75-second limit, reboot/reconnect has a 180-second limit, and
same-SIM activation verification has a 30-second limit. A 420-second process
deadline also bounds uncooperative service cleanup. An installer success alone
does not prove activation. Failures do not trigger repeated installs or restarts.
An accepted operation cannot be undone by timing out; reconnect and run status.

Check voice, data, and IMS service on the phone after a switch. Returning to
MVNO replaces this selector's payload; it does not remove unrelated historical
experiments. SIM, carrier, and OS updates can change selection.

## Other commands

```sh
python3 LightSpeedSwap.pyz doctor
python3 LightSpeedSwap.pyz prepare --to wholesale --output Wholesale-selector.ipcc
python3 LightSpeedSwap.pyz diagnose --seconds 45 --output carrier-diagnostic.json
python3 LightSpeedSwap.pyz --version
```

`doctor` checks the local runtime and included signatures without a phone.
`prepare` creates a private package without installing it. `diagnose` reads a bounded,
redacted trace; review it before sharing. No reports are uploaded. Full IMSI,
ICCID, and UDID are never uploaded. Full IMSI/ICCID appear in the private selector
package and its on-phone directory name, and may appear in device logs; do not
share that IPCC. Host restoration receipts contain only keyed fingerprints.
Firmware lookup sends only the public product model to IPSW.me.

Exit codes: `0` success/already active; `2` prerequisite/error; `3` activation or
reboot unconfirmed; `4` manual reboot required; `124` hard deadline; `130` interrupted.

## Development

```sh
python -m pip install -r requirements.txt
python -m unittest -q
python build_cli.py
```

The build produces only `dist/LightSpeedSwap.pyz` and its checksum. Its stored
outer ZIP members make the output reproducible across Python/zlib versions;
the carrier pack inside is already compressed. Tests never access a phone.
CI covers Python 3.11–3.14 on Linux, macOS, and Windows.

Dependencies: [pymobiledevice3](https://github.com/doronz88/pymobiledevice3),
[cryptography](https://github.com/pyca/cryptography), and optional
[blacktop/ipsw](https://github.com/blacktop/ipsw/tree/v3.1.741).
Apple carrier files remain unmodified and are not relicensed as application code.
