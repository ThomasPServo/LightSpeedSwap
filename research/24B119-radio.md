# On-device radio access in iOS 27.2 (24B119)

This is targeted Ghidra analysis of the actual iPhone19,2 / V63AP firmware,
not a claim that the whole OS or baseband was reversed. The finding is a
plausible ordinary-app **internet QoS** read path. The separate **serving-cell**
path requires `spi`. An app build passing CI does not prove either interface
works on a physical phone.

## Input and integrity

Source: [Apple IPSW](https://updates.cdn-apple.com/2026FallFCS/fdde63b6-6de4-4199-a5d4-7ba4e46b758e/iPhone19,2,iPhone19,3,iPhone19,7_27.2_24B119_Restore.ipsw).
BuildManifest identity: iPhone19,2, v63ap, 27.2, 24B119, Customer Erase Install.
Only the OS and Cryptex1,SystemOS members were fetched using validated HTTP
ranges, with complete ZIP CRC32 and SHA-256 checks, then AEA-decrypted.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `141-68953-104.dmg.aea` | 9,529,458,688 | `7d9e3aa60fc241567578ffd66e4c741b91b8a1aa916ca8b17232279a5ab4b143` |
| `141-68931-108.dmg.aea` | 2,415,919,104 | `b210bb776d8c02ee8c41ff51aa0d43ff3a17f7e921645d6f6a2340404ca7db2e` |
| CommCenter | 40,154,032 | `0a8eead80d5678c496c83cf987b913eacea856070f55ad7fdb69339e982d6c65` |
| FTMInternal | 4,112,656 | `761256b528dd0dee046d798124d3139b704c1438c138627e0108365260114d64` |
| Extracted/rebased CoreTelephony | See extraction note | `61a743bbec256e15c795a284f1f1bae8bb88c2891904ccf6050b40c981ea6336` |

All 9,715 CommCenter and 992 FTMInternal code pages matched their embedded
CodeDirectory hashes. This is a consistency check, not independent Apple
certificate-chain validation. All 82 reused dyld-cache files were byte-hash
matched against the freshly decrypted SystemOS filesystem. CoreTelephony was
extracted with `ipsw dyld extract --slide --objc`; its hash describes that
tool's transformed output, not a standalone Apple-signed framework file.

## Ghidra setup and limits

Ghidra **12.2**, release archive SHA-256
`2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67`,
ran headlessly with JDK 25. The supplied [scripts](ghidra/) import/inventory
symbols, decompile selected functions and inspect permission tables.

This image uses instructions missing from the stock AppleSilicon decoder.
[AARCH64_PAuthLR_analysis.sinc](ghidra/AARCH64_PAuthLR_analysis.sinc) adds a
small analysis-only FEAT_PAuth_LR/FEAT_CPA subset. It uses opaque PAC and checked
pointer-arithmetic operations; it does **not** emulate authentication failures,
pointer signature/tag bits, or hardware traps. Firmware bytes were not patched.
Encodings were cross-checked with LLVM's
[PAuth_LR tests](https://github.com/llvm/llvm-project/blob/main/llvm/test/MC/AArch64/armv9.5a-pauthlr.s)
and [CPA tests](https://github.com/llvm/llvm-project/blob/main/llvm/test/MC/AArch64/armv9.5a-cpa.s).

Some C++ types and tail calls are imperfectly recovered. Extracted CoreTelephony
also branches to shared-cache stubs outside its image, causing decompiler
warnings. Relevant calls, selector strings and branch conditions below were
cross-checked against disassembly and the complete cache. Generated pseudocode
is supporting evidence, not an authoritative source listing.

## Server access checks

`-[CTXPCServer connection:handleInvocation:isReply:]` at **0x10000e310** asks
`requiredEntitlementForRequest:` at **0x10000ce10** for the selector's permission,
then checks the connecting process. It also has a separate ACL stage.
The permission-name table at **0x1022dc7a0** maps 1 to `(public)` and 3 to `spi`.

| Request | Required permission |
| --- | --- |
| `getConnectionState:connectionType:completion:` | 1, public |
| `getDataStatus:completion:` | 1, public |
| `getCurrentDataServiceDescriptor:` | 1, public |
| `getActiveContexts:` | 3, spi |
| `copyCellInfo:completion:` | 3, spi |
| `refreshCellMonitor:completion:` | 3, spi |

Both checker variants delegate to **0x10000f734**: the legacy vtable slot is
**0x100031db0**, the fine-grained checker slot **0x10000f71c**. The public-class
branch at **0x10000f7d0–0x10000f7e4** returns true for class 1 without looking up
the SPI grant. This supports ordinary-app access at this gate; it does not prove
that later ACLs, client capability checks, or modem state will allow a result.
Names alone are insufficient: `copyPublicCellId:` is class 9, not class 1.

## QoS return path

| Stage | Address | Observed behavior |
| --- | --- | --- |
| Client `getInternetConnectionStateSync:` | 0x19df5a4e8 | Checks `supportsRequestWithSelector:`, calls private connection-state helper with internet connection type 0 |
| Client `getInternetDataStatusSync:` | 0x19df55cac | Checks capability, calls private data-status helper |
| Server connection-state request | 0x10006b594 | Fetches data connection; captures `hasEntitlement:3` result for reply filtering |
| Native-to-CTDataConnectionStatus conversion | 0x10006ae54 | Builds CTCellularQoSFlow array, including TX/RX QoS |
| Native-to-CTQoS conversion | 0x1002593a8 | If populated, boxes native QoS class and calls `setQci:` |
| Empty-QoS helper | 0x10025954c | Examines optional QoS fields; no entitlement check in this helper |
| Reply callback | 0x10006acd4 | Builds connection object, then redacts APN for a caller without SPI |

Specific cross-checks: conversion calls at **0x10006b0e0** and **0x10006b10c**
produce TX/RX QoS; **0x10006b16c** calls `setQosFlows:`. The native QoS class
reaches `setQci:` at **0x100259410**, through selector stub **0x101b3f220**.

The callback's SPI test replaces `apnName` with **`--`**. It does not clear the
QoS-flow array in that callback. The implementation therefore reads
`qosFlows → txQos/rxQos → qci`; it never substitutes `flowId` or `bearerId` for
the class and accepts an APN hidden by iOS.

The client helper stubs at **0x1a0090640** and **0x1a0090650** resolve in the
shared cache to `private_getConnectionState:connectionType:error:` and
`private_getDataStatus:error:`. The wrapper's unsupported-request error uses
code 19; the app records the returned domain/code instead of assuming every
error is an entitlement denial.

`supportsRequestWithSelector:` at **0x19df3bda4** delegates to `supportsCellular`
through stub **0x1a00945f0** (selector string **0x1f5a3b8d2**). The private
connection helper at **0x19df44f0c** also checks this capability before making
its synchronous XPC request. This wrapper check is not itself an SPI check.

`CTDataStatus.newRadioNsaDataBearer` at **0x19dfe7670** compares the current
data-bearer technology to 5; `newRadioSaDataBearer` at **0x19dfe7690** compares
it to 6. The app calls these helpers instead of hard-coding those two values.
It requires the same data-service descriptor before/after the read to label
5QI versus QCI. A radio icon or an `is5G` flow flag alone is insufficient.
The LTE `dataMode == 14` fallback is verified in this image: the formatter at
**0x19df525b8** indexes a table with `mode + 2`; entry **0x1e0d69d50** points to
the string `LTE` at **0x19e151ce1**. This private enum still needs revalidation
on other firmware; ambiguous mode is displayed as QCI / 5QI.

## Why connected bands remain unresolved on stock iOS

The cell-monitor request and active-context enumeration both require SPI.
Apple's extracted FTMInternal carries `com.apple.CommCenter.fine-grained`
including `spi`, `internal`, `identity`, `bb-xpc` and other grants. An ordinary
development signature cannot create those Apple-granted capabilities.
Finding Field Test's selectors does not remove the server check.

The native reader reports capability failures and leaves measurements empty.
It does not treat supported/allowed bands as connected bands, guess a band
from an overlapping ARFCN range, forge entitlements, or modify CommCenter.
No automatic stock-iOS on-device connected-band route was established here.
The optional Mac log collector may expose explicit serving-cell events, but
that also needs a real-device test and is not a substitute for native access.

## Reproduce the targeted analysis

Extract the named binaries/cache from the exact manifest-selected images.
Use a separate Ghidra install, append an include for the provided SLEIGH file
to `AARCH64_AppleSilicon.slaspec`, and compile that language with `support/sleigh`.
Import CommCenter as `AARCH64:LE:64:AppleSilicon`, then run, for example:

```sh
analyzeHeadless ./projects RadioServer -process CommCenter -noanalysis \
  -scriptPath ./research/ghidra \
  -postScript RadioDecompile.java ./analysis \
  10000ce10 10000e310 10000f734:10000f7e8 \
  10006b594 10006acd4 10006ae54:10006b34c \
  1002593a8:10025954c 10025954c
```

Use the Mach-O function-start table for bounded ranges; do not reuse these
addresses on a different binary hash. `RadioMemory.java` accepts an output
filename followed by address/count pairs. The repository contains authored
analysis and scripts, not the large firmware images or Apple's full binaries.

The next decisive validation is a normally signed physical-device run:
record build/model, read during cellular traffic, and export the projected
report. A populated QoS flow would establish access for that configuration.
A denial/empty response must remain a denial/empty response, not a guessed
priority class or a claim of compatibility.

## Log-format check following an empty USB report

The actual description methods were also decompiled in Ghidra:
`CTCellularQoSFlow.description` at **0x19dfac7b0**,
`CTQoS.description` at **0x19dfac1ac**, and
`CTDataConnectionStatus.description` at **0x19dfa87c8**.
The shared opening format at **0x1e4165a20** points to `<%@ %p`; flow fields use
`, flowId=%lu`, `, bearerId=%lu`, `, txQos=%@`, and `, rxQos=%@`.
These shapes agree with the original parser. That does not prove they are
emitted over paired USB in a particular capture.

An export containing mode events but no QoS/cell rows cannot establish whether
the original log lacked the fields, hid them, or used an unrecognized shape:
raw messages were deliberately not retained. Version 0.6.1 adds aggregate
candidate/redaction/decoding counters to distinguish those cases more clearly
without retaining raw logs. A redacted TX value must not consume a later RX
object; that parser boundary is now enforced and covered by a regression test.
