# LightSpeedSwap 0.7.1

Intel Mac setup now installs a checksum-pinned, prebuilt cryptography 50.0.2
package with OpenSSL included. This fixes the Rust/OpenSSL build failure reported
with Intel Python 3.14. No Homebrew, Rust, or OpenSSL development installation is
needed for this fix, and no cryptography downgrade is used.

## Upgrade

Download the new **LightSpeedSwap.pyz**, replace your previous download, and run:

```sh
python3 ~/Downloads/LightSpeedSwap.pyz setup
```

This also retries a failed 0.7.0 installation. Keep the existing LightSpeedSwap
data folder: setup preserves your carrier cache and original-bundle receipts.
Once setup passes, use `status`, `setup-device`, and `switch` as before.

## Compatibility changes

- Intel macOS uses one SHA-256-pinned wheel from Espressif's official package
  service. Apple Silicon, Windows x64, and Linux keep their PyPI dependency path.
- A compatible existing private runtime is reused; fresh setup prefers the
  supported Python that launched it. Interpreters incompatible with ABI3 wheels
  are rejected so setup can select a supported private runtime.
- cryptography source builds are disabled: a missing or changed wheel produces
  a setup error instead of a Rust/OpenSSL compilation attempt.
- CI adds Intel Mac Python 3.11–3.14 coverage and actual zipapp repair/repeat setup
  checks on Intel macOS 15 and 26 with Python 3.14. Release publication waits for
  all compatibility checks.

The app stays under 500 KiB. The Intel dependency downloads separately during
setup; Python and other dependencies are still not embedded in the CLI.
Only the universal CLI and SHA256SUMS.txt are release assets.

Carrier switching behavior is unchanged. The owner has confirmed both switching
directions on iOS 27.2/24B119 and SA/UC on a US iPhone 18 Pro. This release adds
host setup coverage; it does not establish physical USB switching on every host
or compatibility with every past/future iOS or macOS version. No phone is accessed
by setup. No backup, restore, OS rollback, or Find My change is required.

See the [README](https://github.com/ThomasPServo/LightSpeedSwap/blob/v0.7.1/README.md)
and [validation record](https://github.com/ThomasPServo/LightSpeedSwap/blob/v0.7.1/VALIDATION.md).
