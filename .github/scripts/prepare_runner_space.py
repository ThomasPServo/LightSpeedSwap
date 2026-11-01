"""Choose temporary firmware space only on disposable GitHub-hosted runners."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


def main():
    system = platform.system()
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or system not in {'Darwin', 'Linux', 'Windows'}):
        raise SystemExit('Space preparation is restricted to GitHub-hosted jobs.')
    candidates = [Path(os.environ['RUNNER_TEMP'])]
    if system == 'Windows':
        # The runner workspace and the OS temporary folder can be on different
        # drives. Use the one with room instead of downloading onto a small D:.
        candidates.append(Path(tempfile.gettempdir()))
    temporary = max(candidates, key=lambda p: shutil.disk_usage(p).free)
    target = 32 * 1024**3
    if system == 'Darwin':
        unused = sorted(Path('/Applications').glob('Xcode*.app'))
    elif system == 'Linux':
        unused = [Path('/usr/local/lib/android'), Path('/usr/share/dotnet')]
    else:
        unused = []
    for path in unused:
        if shutil.disk_usage(temporary).free >= target:
            break
        if path.is_symlink() or not path.is_dir():
            continue
        print('Removing unused runner SDK: ' + str(path), flush=True)
        subprocess.run(['sudo', '-n', '/bin/rm', '-rf', '--', str(path)], check=True)
    free = shutil.disk_usage(temporary).free
    print(f'Firmware temporary directory: {temporary}; free: {free / 1024**3:.1f} GiB', flush=True)
    if free < 25 * 1024**3:
        raise SystemExit('Runner has insufficient firmware space; no firmware download started.')
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as target:
        target.write('CARRIER_PREP_TEMP=' + str(temporary) + '\n')


if __name__ == '__main__':
    main()
