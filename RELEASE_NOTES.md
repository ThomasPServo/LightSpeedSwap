# LightSpeedSwap 0.7.4

Fix exact-build beta firmware discovery and preserve newer carrier settings
through later firmware and carrier updates.

## Upgrade

Replace **LightSpeedSwap.pyz**, then run:

```sh
python3 LightSpeedSwap.pyz setup
python3 LightSpeedSwap.pyz setup-device
python3 LightSpeedSwap.pyz status
```

Use `py -3` instead of `python3` on Windows. Keep the existing state folder;
verified payloads and original-bundle receipts survive the app update.

## Changes

- Exact model/build discovery falls back from IPSW.me to AppleDB when needed.
  Only a unique active Apple HTTPS IPSW for the exact version/build/model is
  selected. Developer beta erase/upgrade manifests are now recognized.
- Future numeric iOS versions, model identifiers, and carrier versions do not
  require an app allowlist update when their formats remain supported.
- `status` compares both prepared versions with the selected line and reports
  whether a refresh is available or a newer signed pair is required. It does
  not claim that locally matching versions are Apple's latest release.
- `setup-device --refresh` explicitly fetches the exact firmware again.
  A supplied `--firmware-url` is honored even when a payload is already cached.
- Firmware refreshes and pack imports reject downgrades of either prepared
  target. The comparison holds the cache lock and preserves updates imported
  while a large firmware download was in progress.
- App updates choose a complete newer pair from included or cached files.
  Conflicting pairs stop and can be resolved by importing a current signed pair.
- Signed minimum-iOS requirements apply to every payload import/read path.
  MVNO and Wholesale can have different versions; each update is compared with
  its own previous version and the selected line's active version.

## Validation and limits

178 offline tests pass on Linux/Python 3.12. The release is gated on the existing
18-job Windows/Linux/Apple Silicon/Intel Mac compatibility workflow.
The catalog matrix covers 35 iPhone identifiers and 16 iOS 27 beta/RC builds:
419/419 beta combinations and 65/69 RC combinations resolve and validate.
Four RC combinations have no unique active IPSW in either index; no other build
is substituted. All 442 available actual IPSW manifests and 2,904 negative
identity/format controls passed. See VALIDATION.md for scope and reproduction.

Manifest checks do not establish beta carrier-signature or physical-switching
support. Unknown Apple signing keys, signature layouts, and firmware formats
still require a reviewed app update; verification is never bypassed. Future
iOS changes to the installation/selector mechanism cannot be guaranteed.

A custom selector remains on the phone after revert and may outrank later broad
carrier updates. Reconnect, check status, and refresh manually when needed.
There is no background updater or automatic discovery of carrier-only IPCCs.

Independent project; not affiliated with Apple, US Mobile, or T-Mobile.
