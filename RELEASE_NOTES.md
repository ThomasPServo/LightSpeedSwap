# LightSpeedSwap 0.7.0

Switch an eligible US Mobile Light Speed iPhone between the original Apple-signed
TMobile_MVNO_US and TMobile_Wholesale_US carrier bundles on iOS 27+.

**5G SA and the 5G UC indicator are tested and known to work on an iPhone 18 Pro,
US model, as confirmed by the project owner.** This is a device-specific physical
report; other models, firmware, provisioning, and coverage still need testing.
The report does not separately identify the CLI revision or radio test method.
A UC icon does not, by itself, establish an SA connection or higher network priority.

## Included

- One universal Python CLI for macOS, Windows, and Linux.
- Native Windows/Linux firmware preparation on x64 and ARM64, with pinned
  `ipsw` and 7-Zip helpers and original Apple signature checks.
- Exact model, board, iOS version, and build matching; a compact 8 MiB/eight-entry
  payload cache and optional GitHub Actions preparation.
- A SIM-specific selector, saved baseline, `revert`, update-pair import, and
  rejection of regular T-Mobile, AT&T, and Verizon as selected targets.
- One installation, one requested restart, and bounded same-SIM verification.

No jailbreak, device backup, restore, OS rollback, or Find My change is required.
CarrierSIM's blocked AirTraffic transfer is not used.

## Download and run

Download **LightSpeedSwap.pyz** and **SHA256SUMS.txt** below. The app is 479,256 bytes
(about 468 KiB); Python and dependencies are installed separately.
Python 3.9+ can start setup. Connect an unlocked iPhone by USB and trust the computer.

```sh
python3 LightSpeedSwap.pyz setup
python3 LightSpeedSwap.pyz status
python3 LightSpeedSwap.pyz setup-device
python3 LightSpeedSwap.pyz switch --to wholesale
```

On Windows, replace `python3` with `py -3` and install Apple Devices for its device
drivers/services. Linux needs a working `usbmuxd` service. Unlock after the restart.
For multiple lines, select the intended `Slot` from `status` with `--slot`.

Return with `switch --to mvno`, or use `revert` for the same SIM's saved baseline.
An unfamiliar firmware requires several GB of download and at least 25 GiB free
temporary space; the large images are removed after successful preparation.

## Evidence and limits

125 offline tests passed across 12 macOS/Windows/Linux and Python 3.11–3.14
configurations. Actual Apple firmware preparation passed on macOS and native
Windows/Linux x64/ARM64 hosts. The owner previously confirmed switching both
directions on iOS 27.2 build 24B119, with reboot required, and now confirms SA/UC
on the US iPhone 18 Pro. Host extraction tests do not establish USB activation
on every host or future firmware compatibility.

Physical eSIM replacement and legacy migration remain separate test gaps.
`revert` selects the baseline bundle; it is not a clean removal of every installed
carrier file. Automatic carrier OTA precedence is not guaranteed. Read the
[README](https://github.com/ThomasPServo/LightSpeedSwap/blob/v0.7.0/README.md)
and [validation record](https://github.com/ThomasPServo/LightSpeedSwap/blob/v0.7.0/VALIDATION.md)
before upgrading from an earlier selector.

CLI SHA-256:

```text
b17d6c5ee14e3f67624fffc8d7614e96270695637a7904b378b5d32e24c5fd00
```

Independent project; not affiliated with Apple, US Mobile, or T-Mobile.
