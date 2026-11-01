#!/usr/bin/env python3
"""Reproducibly build the universal Python CLI without dependencies or a phone."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

RUNTIME_FILES = ('launcher.py', 'setup_env.py', 'carrier_paths.py', 'carrier_https.py', 'carrier_switch.py',
                 'carrier_payloads.py', 'carrier_remote.py', 'carrier_native.py', 'carrier_store.py', 'carrier_signature_audit.py',
                 'carrier_diagnostics.py', 'carrier_hygiene.py', 'cf_string_contract.py', 'requirements.txt',
                 'bundled.carrierpack')


def build(root, destination):
    root, destination = Path(root), Path(destination)
    files = {name: (root / name).read_bytes() for name in RUNTIME_FILES}
    files['app-index.json'] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                                        sort_keys=True, separators=(',', ':')).encode()
    files['__main__.py'] = (root / 'zipapp_main.py').read_bytes()
    out = io.BytesIO()
    out.write(b'#!/usr/bin/env python3\n')
    # The carrier pack is already compressed. Stored outer members keep the
    # app bit-for-bit reproducible across Python/zlib versions and platforms.
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 31, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(out.getvalue())
    destination.chmod(0o755)
    return hashlib.sha256(out.getvalue()).hexdigest()


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    target = root / 'dist' / 'LightSpeedSwap.pyz'
    checksum = build(root, target)
    (target.parent / 'SHA256SUMS.txt').write_text(checksum + '  LightSpeedSwap.pyz\n')
    print(str(target) + ': ' + str(target.stat().st_size) + ' bytes; sha256 ' + checksum)
