"""Reclaim unused Xcode installations only on an ephemeral GitHub-hosted Mac."""
import os
from pathlib import Path
import platform
import shutil
import subprocess


def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or platform.system() != 'Darwin'):
        raise SystemExit('Space reclamation is restricted to GitHub-hosted macOS jobs.')
    temporary = Path(os.environ['RUNNER_TEMP'])
    target = 32 * 1024**3
    for app in sorted(Path('/Applications').glob('Xcode*.app')):
        if shutil.disk_usage(temporary).free >= target:
            break
        if app.is_symlink() or not app.is_dir():
            continue
        print('Removing unused runner SDK: ' + str(app), flush=True)
        subprocess.run(['sudo', '-n', '/bin/rm', '-rf', '--', str(app)], check=True)
    free = shutil.disk_usage(temporary).free
    print(f'Free temporary space: {free / 1024**3:.1f} GiB', flush=True)
    if free < 25 * 1024**3:
        raise SystemExit('Runner has insufficient space after reclaiming unused Xcode apps; no firmware download started.')


if __name__ == '__main__':
    main()
