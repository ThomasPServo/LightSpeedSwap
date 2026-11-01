# LightSpeedSwap 0.7.2

Fix `setup-device` failing with `SSL: CERTIFICATE_VERIFY_FAILED` on macOS Python
installations whose default CA file is missing or incomplete.

## Upgrade

Replace the old **LightSpeedSwap.pyz** with this release and run:

```sh
python3 ~/Downloads/LightSpeedSwap.pyz setup
python3 ~/Downloads/LightSpeedSwap.pyz setup-device
```

Setup refreshes dependencies in the existing private runtime and preserves
carrier caches and original-bundle receipts. No system Python certificate
installer, Homebrew, Rust, or manual runtime deletion is required.

## Changes

- Add certifi 2026.10.19 as an explicit pinned runtime dependency.
- Use the same verified HTTPS context for firmware-index requests, Apple IPSW
  range downloads, and firmware-helper downloads. Load certifi roots alongside
  the default/configured Python roots; retain certificate and hostname checks.
- Check that the CA bundle can load during the offline runtime doctor check.
- Add real in-memory TLS regression tests for missing default roots, preserved
  configured roots, unknown issuers, and incorrect hostnames.
- Extend Intel macOS setup checks to request firmware-index and Apple HTTPS
  resources with default CA paths deliberately missing. No phone is accessed.

The Intel cryptography fix from 0.7.1 is retained. The OpenSSL legacy-provider
warning concerns optional legacy algorithms and is separate from this certificate
trust error. This release does not suppress the warning or disable verification.
Carrier switching behavior and existing device/firmware validation limits remain
unchanged. Host tests do not establish physical USB switching on every host.

Independent project; not affiliated with Apple, US Mobile, or T-Mobile.
