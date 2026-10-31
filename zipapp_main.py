"""Entry point embedded in the single-file Python CLI distribution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def default_home():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'LightSpeedSwap'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'LightSpeedSwap'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'LightSpeedSwap'


def unpack(archive, destination):
    """Extract only flat, explicitly hashed runtime members, never ZIP paths."""
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        names = [m.filename for m in members]
        if (len(names) != len(set(names)) or len(names) > 32
                or sum(m.file_size for m in members) > 16 * 1024 * 1024):
            raise ValueError('Invalid CLI archive size or duplicate members.')
        index = json.loads(z.read('app-index.json'))
        if not isinstance(index, dict) or set(names) != set(index) | {'app-index.json', '__main__.py'}:
            raise ValueError('Invalid CLI archive index.')
        for name, expected in index.items():
            if not name or Path(name).name != name or '/' in name or '\\' in name or name in {'.', '..'}:
                raise ValueError('Invalid CLI archive member path.')
            data = z.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('CLI archive checksum mismatch.')
            (Path(destination) / name).write_bytes(data)


def main():
    if sys.version_info < (3, 9):
        print('LightSpeedSwap needs Python 3.9+ to bootstrap; setup installs a supported runtime.', file=sys.stderr)
        return 2
    environment = dict(os.environ)
    environment.setdefault('CARRIER_SELECTOR_HOME', str(default_home()))
    with tempfile.TemporaryDirectory(prefix='lightspeedswap-') as temporary:
        unpack(Path(sys.argv[0]).resolve(), temporary)
        return subprocess.call([sys.executable, '-s', str(Path(temporary) / 'launcher.py'), *sys.argv[1:]],
                               env=environment)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        print('CLI stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
