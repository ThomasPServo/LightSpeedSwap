#!/usr/bin/env python3
"""Portable CLI launcher; setup is explicit and never accesses a phone."""
from pathlib import Path
import subprocess
import sys

from carrier_paths import data_root
from setup_env import clean_environment, executable


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv) or ['status']
    root = Path(__file__).resolve().parent
    environment = clean_environment()
    if argv[0] == 'setup':
        return subprocess.call([sys.executable, '-s', str(root / 'setup_env.py'), *argv[1:]],
                               env=environment)
    if argv[0] in {'-h', '--help', '--version'}:
        return subprocess.call([sys.executable, '-s', str(root / 'carrier_switch.py'), *argv],
                               env=environment)
    python = executable(data_root(root) / '.carrier-runtime' / 'env')
    if not python.is_file():
        print('Run the app with "setup" first to install its private Python runtime. '
              'No phone operation was attempted.', file=sys.stderr)
        return 2
    return subprocess.call([str(python), '-s', str(root / 'carrier_switch.py'), *argv],
                           env=environment)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, ValueError) as exc:
        print('Launcher stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
