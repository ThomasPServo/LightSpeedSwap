# LightSpeed Radio for iPhone

Experimental native SwiftUI app with **Read this iPhone**, QoS flows, reported
serving bands, per-SIM filtering, capability results and JSON export/import.
It performs direct CoreTelephony reads on the phone; the Mac collector is optional.

[Actual Ghidra findings for 27.2 / 24B119](../research/24B119-radio.md) identify a
public-server QoS route and a separate SPI requirement for serving-cell reads.
Physical-device success is not yet verified. The app can therefore return QoS
while bands are unavailable, or return no measurements. There is no claim of
universal private-API access on every iPhone/firmware.

## Build and install

1. Open `LightSpeedRadio.xcodeproj` in Xcode on a Mac with the device's supported SDK.
2. Select the LightSpeedRadio target, choose your signing team and a unique bundle
   identifier under Signing & Capabilities, and leave automatic signing enabled.
3. Choose the connected iPhone as destination, enable Developer Mode if iOS asks,
   then Run. No Apple-private entitlements are included or needed for installation.
4. Open the app, use cellular data and tap **Read this iPhone**. The refresh button
   enables five-second reads while the app stays in the foreground.
5. If readings are missing, inspect capability states and export the report from
   the menu. Error domains/codes are included; raw errors and subscriber IDs are not.

The project has an iOS 17 deployment target so the UI can be built/tested broadly;
the private radio path was analyzed only for the stated 27.2 firmware. CI builds
the simulator and device app, runs Swift tests, and attaches an **unsigned IPA**
for personal signing. That IPA cannot be installed as-is. This private-API
research app is not presented as an App Store release.

## What readings mean

- `qci` comes from CTQoS attached to the current internet connection, not a speed
  estimate, carrier-bundle setting, QFI, bearer ID or subscription plan lookup.
- 5QI is shown only with a stable data service and SA bearer evidence. NSA uses
  QCI. Ambiguous mode preserves the class and displays **QCI / 5QI**.
- An APN of `--` means iOS hid its name; the app displays **APN hidden by iOS**.
- Only explicit serving/primary/secondary cell records supply serving bands.
  A supported-band list is never displayed as a live connection.
- Timestamps are read times. iOS may return cached modem data. Readings become
  old after 30 seconds, and polling stops in the background.
- The reader cannot change network-assigned QoS. Carrier switching is still a
  separate CLI operation and does not establish that a plan's priority changed.

## Optional Mac and saved reports

Run `python3 LightSpeedSwap.pyz dashboard --share` on the Mac. On the same trusted
Wi-Fi, paste the printed phone link into **Connect to Mac**. The session key stays
in memory; redirects are refused. LAN sharing uses HTTP, so use a trusted network.
Imported JSON is always historical and never pretends to be a current read.

The native bridge uses a small read-only selector whitelist, ABI checks, bounded
projections and an eight-second response deadline. A stuck synchronous call is
not retried on accumulating threads. Missing/changed interfaces fail with an
explicit capability state. No backup, restore, carrier install, OS downgrade or
Find My change is performed. No raw system logs, IMSI, ICCID, SIM UUID, phone number,
cell ID or TAC are exported by the direct reader.

## Development checks

```sh
swift test --package-path ios
xcodebuild -project ios/LightSpeedRadio.xcodeproj -scheme LightSpeedRadio \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

See [Native iOS reader CI](https://github.com/ThomasPServo/LightSpeedSwap/actions/workflows/ios.yml)
for the exact build result and unsigned device artifact. Compilation and synthetic
projection tests do not substitute for a physical phone measurement.
