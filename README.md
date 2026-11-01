# LightSpeedSwap

A compact Python CLI for switching **TMobile_MVNO_US ↔ TMobile_Wholesale_US**
on an eligible iPhone running **iOS 27 or later**. It detects the active bundle,
uses original Apple-signed files for the exact firmware, installs once, restarts
once, and verifies the selected SIM afterward.

**No backup, restore, OS downgrade, or Find My change.** `--skip-backup` remains
accepted for compatibility; every command always skips backups.

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

On Windows (PowerShell or Command Prompt):

```powershell
py -3 LightSpeedSwap.pyz setup
py -3 LightSpeedSwap.pyz status
py -3 LightSpeedSwap.pyz setup-device
py -3 LightSpeedSwap.pyz switch --to wholesale
py -3 LightSpeedSwap.pyz switch --to mvno
```

**Firmware preparation runs locally on macOS, Windows, and Linux.** Windows
needs Apple's device drivers/services (installed with Apple Devices); Linux
needs working usbmuxd and permission to access its socket. These USB prerequisites
are separate from firmware extraction. USB behavior is not established by
offline tests or by successfully preparing a carrier pack.

| Host | Firmware preparation |
| --- | --- |
| macOS, Apple Silicon or Intel | Read-only APFS mount using `hdiutil` |
| Windows, x64 | Direct APFS reading with pinned `ipsw` and 7-Zip helpers |
| Windows 11, ARM64 | Same reader; setup selects x64 Python for dependency compatibility |
| Linux, x86-64 or ARM64 | Direct APFS reading with pinned native helpers |

Windows/Linux extraction needs no Mac, WSL, virtual machine, filesystem driver,
FUSE mount, or administrator/root access. On Windows ARM, the private Python
runtime uses Windows' x64 emulation because the pinned cryptography release has
no ARM64 wheel. The setup bootstrap handles this without downgrading dependencies.

Unlock and trust the connected iPhone. Unlock it again after the restart.
`--no-reboot` leaves the restart to you and reports it as pending. If the target
bundle is already reported active, nothing is installed; use `--reinstall` only
when you explicitly want another installation.

From source, use `python3 launcher.py setup` and `python3 launcher.py status`.
The existing `bash Setup.command` and `bash Run.command ...` launchers also work.

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
decryption and extraction, the large images are deleted. Windows/Linux never
mount the image. On macOS, uncertain unmount cleanup preserves the backing image
and reports its location.

The optional firmware helpers are downloaded on demand into `.carrier-tools`
under the state directory. They use about 83–109 MiB, depending on the host:
`ipsw` 3.1.741 decrypts AEA, and Windows/Linux additionally use 7-Zip 26.05 to
read APFS. Archive, executable, and DLL SHA-256 hashes are pinned and checked
before use. Windows' 7-Zip installer is unpacked as an archive; it is never run
as an installer or added to PATH. Only the two named carrier directories are
extracted, then their original Apple signatures are checked again.

Runtime/dependencies, helpers, and manually exported packs are outside the
payload cache cap. No helper binary or full firmware image is shipped in this
repo or embedded in the CLI. An unfamiliar image container, unsupported APFS
feature, or changed helper output stops preparation without writing to the phone.

## Prepare bundles with GitHub Actions

Use [Prepare carrier pack](https://github.com/ThomasPServo/LightSpeedSwap/actions/workflows/carrier-pack.yml)
to do the large download and extraction on a GitHub-hosted Mac, Windows, or
Linux runner. Run `status` locally, then choose **Run workflow**, select `host`,
and enter its four public fields:
`product`, `hardware`, `version`, and `build`. Do not enter SIM identifiers.
Leave `firmware_url` blank for exact-build lookup, or provide the public Apple
IPSW URL for a beta/RC that is not indexed. Running the workflow requires write
access to the repository; other users can run it in their own fork.

After the job succeeds, download and unzip its `LightSpeedSwap-carrier-pack-…`
artifact. It contains one small `.carrierpack` with both bundles, `provenance.json`,
and `SHA256SUMS.txt`. With the matching phone connected, import it and switch:

```sh
python3 LightSpeedSwap.pyz setup-device --bundle-pack /path/to/LightSpeedSwap-….carrierpack
python3 LightSpeedSwap.pyz switch --to wholesale
```

The CLI verifies the pack again against the connected phone's exact model,
board, version, and build. The same pack supports `--to mvno`. The workflow
does not contact a phone or create a SIM selector. The existing CLI already
supports this import; no app update is needed.

The job fetches a fresh firmware image even if that identity is included in the
CLI. It checks the manifest, complete downloaded ZIP member CRC, both original
Apple signature sets, and board overrides before publishing. The receipt records
image/manifest/file hashes and the workflow commit. It uploads no IPSW, decrypted
image, or device identifiers, and does not add generated packs to git. Artifacts
expire after 14 days, so keep your downloaded pack if you need it later.

The runner checks free space first. When needed, the Mac runner reclaims unused
Xcode applications and the Linux runner reclaims unused Android/.NET SDKs.
Windows selects the temporary drive with the most free space. This space-preparation
script refuses to run outside disposable GitHub-hosted jobs. Extraction stops
before downloading if insufficient space remains; unusually
large future images can need more space. Unknown formats or failed validation
produce no artifact. Prepared packs establish file compatibility, not successful
on-phone activation. Firmware bundles may also be older than a carrier-only
update; the existing downgrade checks still apply.

For a request tracked in git, edit only the public fields in
[`.github/carrier-pack-request.json`](.github/carrier-pack-request.json) and commit
it to `main`. This also triggers preparation. Ordinary source commits do not
start a large firmware download. The standalone builder can run on a local Mac,
Windows PC, or Linux machine (use x64 Python on Windows):

```sh
python -m pip install cryptography==50.0.2
python build_carrier_pack.py --request .github/carrier-pack-request.json --output-dir ./prepared-pack
```

The output directory must not already exist. The runner space-cleanup script is
not needed for local use. Each local host still needs the temporary disk space
described above.

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
[blacktop/ipsw](https://github.com/blacktop/ipsw/tree/v3.1.741) and
[7-Zip](https://github.com/ip7z/7zip/releases/tag/26.05) helpers.
The downloaded 7-Zip helper retains its original `License.txt`; its source and
licensing terms are available in the [upstream repository](https://github.com/ip7z/7zip/blob/26.05/DOC/License.txt).
Apple carrier files remain unmodified and are not relicensed as application code.
