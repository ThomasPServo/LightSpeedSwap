# Validation for 0.6.1

The 0.5.0 baseline had **104 offline tests passing on Linux with Python 3.11,
3.12, 3.13, and 3.14**. The 0.6.0 suite adds 25 radio contracts (129 total).
**All 129 passed locally with Python 3.12.**
The single-file CLI was built, bootstrapped into a clean private runtime, and
used for `doctor` and cache inspection. No physical phone was connected here.
GitHub Actions also runs the source suite on macOS, Windows, and Linux; its
actual run status, not this document, determines whether those jobs passed.

## Radio reader validation

0.6.1 adds four regression tests for mode-only streams, missing/redacted TX
values followed by RX, diagnostic counters/reset/privacy, and measurement aging.
All **133 tests passed locally with Python 3.12**. Desktop and phone browser
checks confirmed the mode-only state, separate counters, and redacted-TX/visible-RX
display without overflow or browser errors.
The prior `observations_seen` field includes mode events and remains for format
compatibility; `measurements_seen` explicitly counts only QoS/cell events.
No QoS/band success is inferred from a stream with mode events alone.

- Actual 24B119 CommCenter and extracted CoreTelephony were analyzed in Ghidra
  12.2, including request permissions, the QoS conversion and APN-redaction
  callback. See [addresses, hashes, decoder limits and reproduction](research/24B119-radio.md).
- Python radio tests cover nested QoS parsing, QCI/QFI separation, same-SIM mode
  evidence, timestamps, stale/neighbor cells, identifier exclusion, bounded
  retention/imports and token-protected HTTP access.
- Nine Swift projection tests cover direct-read normalization, NSA/SA ambiguity,
  SIM changes, historical imports, denied requests, invalid/Boolean measurements,
  duplicate identities and local collector links.
- The native prototype built for iPhone simulator and unsigned iPhone device in
  [CI run 41258583246](https://github.com/ThomasPServo/LightSpeedSwap/actions/runs/41258583246).
  Later commits use the same workflow; check their run result for final validation.
- The browser dashboard was exercised at 1280×1000 and 390×844 using explicit
  synthetic data. Both displayed n41/n71 and TX/RX class values, with no browser
  errors or horizontal overflow. These screenshots are not phone measurements.

No phone was connected to the development environment for this work. Neither
native nor paired-USB QoS/serving-band measurements were physically verified.
Firmware analysis establishes a
candidate access route, not successful runtime access on all models or builds.
The read-only path does not change the existing carrier-switch safeguards.

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

The user's earlier physical confirmation applies to the earlier prefix-selector
release, not the newly narrowed selector, migration, automated reboot, or future
models. These changes need physical confirmation. Filesystem extraction/mount
on a Mac and cross-carrier cellular service also need on-device/on-host testing.

## Dependencies and sources

Top-level versions: pymobiledevice3 11.49.1 and cryptography 50.0.2. Runtime setup
uses a private environment. The optional blacktop/ipsw 3.1.741 Mac helper has
pinned archive and executable SHA-256 hashes in `carrier_payloads.py`.

- [IPSW extraction implementation](https://github.com/blacktop/ipsw/blob/v3.1.741/internal/commands/extract/extract.go)
- [AEA implementation](https://github.com/blacktop/ipsw/tree/v3.1.741/pkg/aea)
- [IPSW release](https://github.com/blacktop/ipsw/releases/tag/v3.1.741)
- [pymobiledevice3 installation service](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/installation_proxy.py)
- [Unfamiliar 24A437 Apple image](https://updates.cdn-apple.com/2026FallFCS/f35a2940-4b73-46b3-8ac7-01296d8a033b/iPhone13,2,iPhone13,3_27.0_24A437_Restore.ipsw)
- [Unfamiliar 24B91020 Apple image](https://updates.cdn-apple.com/2026FallFCS/8d0335dc-f553-4d16-b07b-28951fde8f9e/iPhone19,4_27.2_24B91020_Restore.ipsw)
