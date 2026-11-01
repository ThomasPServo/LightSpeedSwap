# LightSpeedSwap 0.7.3

Fix `setup-device` stopping at the 45-minute firmware download limit even while
the download is progressing, as reported on Windows with iPhone16,1 / iOS 27.0.1 /
24A446. The old code also discarded the unfinished image on failure.

## Upgrade

Replace the old **LightSpeedSwap.pyz** with this download, then on Windows run:

```powershell
py -3 LightSpeedSwap.pyz setup
py -3 LightSpeedSwap.pyz setup-device
```

Use `python3` in place of `py -3` on macOS/Linux. Keep the existing state folder;
setup preserves verified carrier caches and original-bundle receipts. The CLI
remains under 500 KiB. This release also includes the 0.7.1 Intel dependency and
0.7.2 HTTPS certificate fixes.

## Download fixes

- Remove the total 45-minute download deadline. Each range request still has a
  45-second socket inactivity timeout and at most three attempts for transient
  network/server errors. Certificate, redirect, and range validation stay on.
- Keep one pending filesystem-member download when setup is interrupted or the
  network fails. Rerunning `setup-device` resumes both stored and deflated ZIP
  members without downloading the saved prefix again. This is explicit resume,
  not a background downloader or repeated phone operation.
- Bind the checkpoint to the URL, archive size, and ZIP member identity, offset,
  sizes, compression, and CRC. Recheck the full member CRC before extraction;
  reject and discard corrupted saved data. Original Apple carrier-signature and
  exact firmware-identity checks remain required before a bundle enters cache.
- Remove the checkpoint after a successful download and temporary expanded
  images after preparation. `cache clear-download` discards the pending download
  while preserving verified payloads and restoration receipts. One pending image
  is outside the 8 MiB payload limit and can occupy several GB until resumed or
  explicitly cleared. Disk-space checks include both checkpoint and output.

The failed 70.9% download from 0.7.0 was deleted by that version and cannot be
recovered retroactively. These host-download fixes do not establish physical
switching on the reported model/build. Setup-device does not install to a phone.

Independent project; not affiliated with Apple, US Mobile, or T-Mobile.
