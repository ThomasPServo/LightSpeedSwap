# Validation for 0.7.1

The repository and universal CLI contain the carrier-switching functionality,
its firmware preparation and signature checks, SIM hygiene, and related diagnostics.
The separate radio dashboard, native reader, and radio research have been removed.
The 0.7.0 carrier-switching suite contained 125 offline tests. No physical phone was
connected to the automated test hosts. Its 12 configurations in the
[GitHub compatibility run](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316884588)
passed on macOS, Windows, and Linux with Python 3.11–3.14.

## Intel Mac setup in 0.7.1

The dependency resolver selects a single, SHA-256-pinned Espressif wheel only
for a Darwin/x86_64 interpreter. It remains cryptography **50.0.2**; other hosts
use the existing PyPI pin. pip checks the URL hash before installing and is
instructed not to build cryptography from source. A failed 0.7.0 dependency
installation can be repaired by running the new app's `setup` command again.
The installer keeps the existing compatible private interpreter and does not
remove the carrier cache or baseline receipts.

- Vendor: [Espressif's maintained Intel build](https://github.com/espressif/idf-python-wheels#macos-intel-x86_64-and-cryptography).
- File: `cryptography-50.0.2-cp311-abi3-macosx_10_12_x86_64.whl`.
- Bytes: 3,716,685.
- SHA-256: `40d870c49cc78ea7f3db421e7a1ae2bd12d9548157714de729c3b7cccd469af0`.
- All 110 `.py`/`.pyi` files match the official PyPI 50.0.2 source distribution.
- PyPI source SHA-256: `7b46165bb56eb4704e2eaaf86f3c940d19154535d9b0ca7d6d590b04060e00d5`.

This is a third-party build of the current dependency, not renewed Intel support
from PyCA. The hash pins reviewed bytes; it is not a claim that the native
binary was reproduced bit for bit. Intel native imports, Apple's bundled
signature checks, and external library linkage are checked in our own CI.
The `.pyz` remains under 500 KiB because the wheel is downloaded at setup time.

The expanded workflow has 16 Python 3.11–3.14 jobs across Linux, Windows, Apple
Silicon macOS, and Intel macOS 15, plus two actual zipapp setup jobs on Intel
macOS 15 and 26/Python 3.14. Setup tests begin with an incomplete environment,
disable access to compilers and OpenSSL development files, run setup twice,
and compare the original receipts/cache afterward. Publication is gated on all
18 jobs. The run evidence is recorded below when validation completes.

## Physical SA and UC confirmation

On 2026-11-01, the project owner confirmed that **5G Standalone (SA) and the
5G UC indicator were tested and are working on an iPhone 18 Pro, US model**,
with US Mobile Light Speed. This is a positive physical-device report, separate
from the offline tests and firmware configuration inspection below.

The earlier session also confirmed switching in both directions on
`iPhone19,2 / V63AP / iOS 27.2 / 24B119`, with a reboot needed. The new SA/UC
report does not separately specify its CLI revision, firmware build, or radio
test procedure, and no modem trace was supplied for independent analysis.
It establishes a known-working owner-reported result, not all-model coverage,
a measured priority/speed change, or validation of eSIM replacement and legacy
migration. The CLI itself checks the active bundle; it does not measure SA,
connected bands, QCI, or 5QI.

## Local Windows and Linux preparation

`setup-device` and the standalone pack builder use pinned native `ipsw` 3.1.741
for AEA decryption and 7-Zip 26.05 for direct APFS reading on Windows/Linux.
No filesystem mount, FUSE/kernel driver, WSL, or Mac is involved in this path.
The helper archives and individual binaries/DLLs are SHA-256 pinned. Windows'
official 7-Zip package is unpacked as data; its installer is never executed.

The [native firmware run](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316598947)
at `0c45a83afdc69b1a4a6b6ffee373644700233306` fetched the actual **iPhone19,2 /
V63AP / iOS 27.2 / 24B119** filesystem from Apple. Each successful job checked
the download CRC, AEA decryption, APFS extraction, all six signature groups,
original-byte comparison, and image cleanup:

| Host | Fresh firmware preparation |
| --- | --- |
| Ubuntu 24.04, x86-64 | [Passed](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316598947/job/124877850490) |
| Ubuntu 24.04, ARM64 | [Passed](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316598947/job/124877850246) |
| Windows Server 2025, x64 | [Passed](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316598947/job/124877850386) |
| Windows 11, ARM64 | [Passed](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41316884511/job/124878703073) |

Windows ARM was verified separately at
`793649ba8c6accbd3c233a7b3e59c220f1ba23a1`, which adds a bounded retry when
Windows briefly locks the temporary unpacker after it exits. That job started
with native ARM64 Python, ran the app's managed x64-runtime bootstrap, passed
all 125 tests in that runtime, then used the native ARM64 `ipsw` and 7-Zip
helpers for actual firmware preparation. No older cryptography package was used.

All four generated artifacts were also downloaded independently. Their GitHub
artifact digests and internal checksums verified, all six Apple signature groups
verified again, and each complete decoded pack matched the earlier macOS artifact.
The ZIP digests differ because the archive headers include preparation timestamps;
the manifests and original carrier bytes are identical. Each pack is 198,566 bytes.
The downloaded filesystem SHA-256 was
`7d9e3aa60fc241567578ffd66e4c741b91b8a1aa916ca8b17232279a5ab4b143`.
These tests establish host preparation for this actual image; no Windows/Linux
phone was connected and no claim of physical USB activation is made.

Independently checked artifact ZIP SHA-256 values (artifacts expire after seven days):

| Host | Artifact SHA-256 |
| --- | --- |
| Linux x86-64 | `3b2ab85747676b948910180e5c9e3dad695a8795d55cf7ad70e1becff7aab58e` |
| Linux ARM64 | `58648648f6b6e8598332534a438ce6602308cbf706fe9fb22bcb0a0be5155b00` |
| Windows x64 | `ae78861e97dc381257ff0508a0bab8a12d5391231873ff61ec6f7b8ecafd92ee` |
| Windows ARM64 | `9ba16776ae490a9886117fc149b40557a693ebce8179ec1f822422047fe705e2` |

Native-reader regression tests cover APFS POSIX modes, alternate attributes,
path traversal, links, other carriers, ambiguous volumes, duplicate/oversized
members, exact output inventories, original signed bytes, helper tampering,
process time/output limits, and bounded Windows executable cleanup. Two runtime
tests cover choosing x64 Python on Windows ARM because the pinned cryptography
version does not publish an ARM64 wheel. Unknown image/metadata formats stop
preparation before a phone write.

## GitHub firmware preparation

`Prepare carrier pack` runs the standalone builder on a selected GitHub-hosted
macOS, Windows, or Linux runner,
fetching the selected Apple image and exporting both verified bundles. Its nine
offline tests cover strict public-only inputs, fresh downloads despite an included
payload, wrong-firmware rejection before image download, corrupted signatures,
mismatched pairs, safe output handling, mount-failure retention, checksums, and
round-trip import through the existing CLI payload code. Large images are mocked
in those tests; the carrier files and signature verification are real.

The separate [live preparation run](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41283236191)
at commit `696ca6d4010d2a92f78c83ed6e02ef229ce34d75` completed successfully on
2026-11-01 for **iPhone19,2 / V63AP / iOS 27.2 / 24B119**. It took about 19 minutes
on a GitHub-hosted macOS 26 ARM64 runner. This was a fresh Apple download, AEA
decryption, read-only filesystem mount/extraction, signature check, successful
unmount, and image cleanup. The runner already had 95.9 GiB free, so its space
check did not need to remove Xcode.

- Filesystem: `141-68953-104.dmg.aea`, 9,529,458,688 bytes, CRC32 `20fba432`.
- Filesystem SHA-256: `7d9e3aa60fc241567578ffd66e4c741b91b8a1aa916ca8b17232279a5ab4b143`.
- Pack: `LightSpeedSwap-iPhone19-2-v63ap-27.2-24B119.carrierpack`, 198,566 bytes.
- Pack SHA-256: `767395064472875b66f191ea8c2cc74bc12fd4017fa0cdfb5434a9c9d834cfad`.
- Artifact ZIP: 202,637 bytes; SHA-256
  `e86a2424f692ceeaa6f6fa78cf9b6874c868b7582a198aee0ea57b005d7fbe65`.

The artifact was downloaded independently and checked against GitHub's artifact
digest and its internal checksums. It contained exactly the pack, provenance,
and checksum files. Both carrier bundles are version 73.0, all six signature
groups verified again, and their file bytes matched the previously verified
included files. Import through the existing payload code succeeded on Linux
without a phone. The image hash also matched the earlier independent download.

This establishes preparation and host import for that identity, not new physical
activation evidence or automatic support for unknown future firmware formats.
Generated artifacts expire after 14 days; no full image was uploaded or committed.

## Actual firmware coverage

| Product / board | iOS / build | Carrier version | Evidence |
| --- | --- | --- | --- |
| iPhone19,2 / V63AP | 27.2 / 24B119 | 73.0 | Original signed payloads; user previously confirmed both directions with reboot |
| iPhone19,3 / V64AP | 27.2 / 24B119 | 73.0 | Original signed payloads; offline only |
| iPhone19,7 / V64sAP | 27.2 / 24B119 | 73.0 | Original signed payloads; offline only |
| iPhone12,1 / N104AP | 27.2 / 24B119 | 73.0 | Actual separate Apple image; offline only |
| iPhone13,2 / D53GAP | 27.0 / 24A437 | 72.0 | Previously unfamiliar image, imported and tested in this release |
| iPhone13,3 / D53PAP | 27.0 / 24A437 | 72.0 | Same unfamiliar image, separate manifest/board identity verified |
| iPhone19,4 / V68AP | 27.2 / 24B91020 | 73.0 | Previously unfamiliar separate-device build, imported and tested in this release |

The unfamiliar identities were first confirmed absent from the old bundled
pack. Their filesystem members were obtained from Apple, complete ZIP member
CRC32 checked, AEA-decrypted, and read as APFS. Both original carrier bundles
passed all Apple P-384 signature groups, board applicability, cache import,
and byte-preserving IPCC construction. No signed payload edits were required.
Full public BuildManifest bytes and official source URLs are retained in the
small content-addressed carrier pack; no complete firmware image is shipped.

Unfamiliar-image receipts:

| Build | Filesystem member | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| 24A437 | `043-69280-657.dmg.aea` | 8,321,499,136 | `3e4720f0be6652923a166aa93c8e202d61ad4865089e4239f5a6527ecfed490d` |
| 24B91020 | `141-28898-095.dmg.aea` | 9,529,458,688 | `d78cb6f8a4d9148d580c24572a5a2a5ecab2c0fc96ac1ff7bb146908f9fc02fb` |

Both manifests were also independently fetched through the new bounded Python
range reader and matched the previously extracted manifest bytes exactly.
The external extractor was killed during large downloads; a bounded research
resume helper completed those images and checked their full CRCs. The app now
uses its own sequential bounded range reader for ZIP members. This is not a
claim that the entire automatic macOS pipeline was executed on this Linux host.

## Regression coverage

- Original bytes/signatures, both directions, board groups, exact-build routing,
  ambiguous Recovery/normal images, wrong firmware and tampered files.
- Deduplication, entry/byte caps, eviction, atomic writes, invalid schemas,
  duplicate index keys, oversized data, missing objects and archive paths.
- Stored/deflated ZIP64 streaming, full-response rejection, exact range/length,
  archive-size changes, CRC failures and actual-member disk budgets.
- Reproducible CLI builds, execution, private-state paths, archive extraction
  validation, no automatic dependency install or phone access before setup.
- One install/reboot, bounded timeouts, same-SIM/build verification, incomplete
  metadata, manual reboot, explicit errors, and uncooperative async cleanup.
- Full-SIM selector isolation, no plaintext identifiers in restoration receipts,
  original-baseline preservation, replacement-device/SIM receipt isolation,
  software-change reporting and switch/revert integration with a fake phone.
- Newer active carrier version rejection, signed update-pair import, preservation
  of old cache on rejected downgrade, refresh with unchanged bundle identifier,
  and rejection of stale post-install version observations.
- Regular T-Mobile, AT&T and Verizon selected-line rejection in both directions;
  secondary-line identity isolation; ambiguous legacy migration rejection.

Synthetic iOS 28/30 labels test parser compatibility only. Unknown variants,
signature generations/keys, and cache formats stop before device installation.
They do not establish support for unreleased firmware behavior.

## SIM hygiene firmware check

The original 24B119 CommCenter parser/scoring excerpts were executed in Unicorn
with synthetic full-SIM selectors. Its SHA-256 was
`0a8eead80d5678c496c83cf987b913eacea856070f55ad7fdb69339e982d6c65`.
The matching synthetic SIM scored 88.0. Changing the ICCID's last digit while
keeping old prefixes scored 0.0; changing the IMSI's last digit scored 0.0;
empty IMSI/ICCID scored 0.0. External CoreFoundation/libc calls were modeled,
with the corrected empty-string rule. This is not whole-iOS emulation or a
physical eSIM removal test. The native comparator remains prefix-based.

The actual 24B91020 `mobile_installation_proxy` binary exposes the carrier
install route and `CTServerConnectionInstallCarrierBundle`. Its uninstall
route is an application-uninstall route. No validated paired-USB carrier
uninstall API was established. The app therefore does not pretend that a
receipt, full-SIM selector, migration, or `revert` deletes all carrier files or
restores exact pre-run filesystem state. It cannot refresh an override while
closed/disconnected. Automatic carrier OTA precedence is not guaranteed.

The earlier bidirectional-switch report applies to the prefix-selector release.
The later SA/UC confirmation is recorded above without assuming a CLI revision.
Physical tests of the narrowed selector's isolation, migration, eSIM replacement,
automated reboot, and cross-carrier service remain separate coverage gaps.
The firmware runs above establish extraction on their listed
hosts for the requested identity; they do not test every future image or phone.

## Dependencies and sources

Top-level versions: pymobiledevice3 11.49.1 and cryptography 50.0.2. Runtime setup
uses a private environment. Optional blacktop/ipsw 3.1.741 and 7-Zip 26.05
helpers have pinned archive, executable, DLL, and (for 7-Zip) license hashes in
`carrier_payloads.py` and `carrier_native.py`.

- [IPSW extraction implementation](https://github.com/blacktop/ipsw/blob/v3.1.741/internal/commands/extract/extract.go)
- [AEA implementation](https://github.com/blacktop/ipsw/tree/v3.1.741/pkg/aea)
- [IPSW release](https://github.com/blacktop/ipsw/releases/tag/v3.1.741)
- [7-Zip APFS reader](https://github.com/ip7z/7zip/blob/26.05/CPP/7zip/Archive/ApfsHandler.cpp)
- [7-Zip release](https://github.com/ip7z/7zip/releases/tag/26.05)
- [pymobiledevice3 installation service](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/installation_proxy.py)
- [Unfamiliar 24A437 Apple image](https://updates.cdn-apple.com/2026FallFCS/f35a2940-4b73-46b3-8ac7-01296d8a033b/iPhone13,2,iPhone13,3_27.0_24A437_Restore.ipsw)
- [Unfamiliar 24B91020 Apple image](https://updates.cdn-apple.com/2026FallFCS/8d0335dc-f553-4d16-b07b-28951fde8f9e/iPhone19,4_27.2_24B91020_Restore.ipsw)
