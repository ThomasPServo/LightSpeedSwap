"""Pinned native helpers and mount-free APFS extraction for Windows/Linux.

Helpers are downloaded from their original releases, never embedded in the CLI.
Only the two requested carrier directories are read. No driver, FUSE mount,
installer, registry change, or administrator access is used here.
"""
import io
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import zipfile

from carrier_paths import data_root
from carrier_signature_audit import MAX_BYTES, classify
import carrier_store as store

IPSW_VERSION = '3.1.741'
SEVENZIP_VERSION = '26.05'
IPSW_BASE = 'https://github.com/blacktop/ipsw/releases/download/v' + IPSW_VERSION + '/'
SEVENZIP_BASE = 'https://github.com/ip7z/7zip/releases/download/' + SEVENZIP_VERSION + '/'
BOOTSTRAP_SHA256 = '9bf582b894dfa665f852d7e9112b02290a1c303c2d95a21fcdc41ffcb03143b7'
LICENSE_LINUX = '1790374e5352329cedb46ee3808930a88e9ca2f08b82b10fcf5cf605d2c301b1'
LICENSE_WINDOWS = '519ac0a4bded9c18ea02e0afb71f663d8c47373bd9facd3ac96a79f51d77765d'

IPSW = {
    ('Linux', 'x86_64'): ('ipsw_3.1.741_linux_x86_64.tar.gz',
        '992a93c708861c14118ebd69c3493d2f5d0af08f57fb345839630ece3e227b20',
        {'ipsw': '476ac8c21660b83be37a5bcdc3e875c13108310369facc0625756cd41506dea0'}),
    ('Linux', 'arm64'): ('ipsw_3.1.741_linux_arm64.tar.gz',
        '2810d5b8b1de4f92c8120f3a93627895fdb129fefa5d012fc90b96fe77c4e06e',
        {'ipsw': '5fc7c85ba0aa690660edb7d0db0853af90108936bc28b1ee4e134b05c441c35a'}),
    ('Windows', 'x86_64'): ('ipsw_3.1.741_windows_x86_64.zip',
        '9e0b8d2e7feda36e58f1d0f25660d1568ba2ff5ef0f7f941fd0b62cf784d7b7e',
        {'ipsw.exe': '2f55a19d3950d55b2ed3f2ff4d00de0ad4d66bbe95e5315a8b3dd4ddafbfe7ba'}),
    ('Windows', 'arm64'): ('ipsw_3.1.741_windows_arm64.zip',
        'be84cd7a170e2ceb76d7353dc9197e2389ea4a39c8dc0bc7de42269156eef410',
        {'ipsw.exe': 'f6b393f39760dc87562a70056c276303bf0537a94e6bd6fad4ce12c771f9ad76'}),
}
SEVENZIP = {
    ('Linux', 'x86_64'): ('7z2605-linux-x64.tar.xz',
        '22cf82fcc6eb7474f1d5bb4f02c0f8ebd573a718089d6833dc30f8350a3b232e',
        {'7zzs': '8952c73646dc8d4abfb31e48a949ba0ac15cb2437da9e4430886ddb6e12b383b', 'License.txt': LICENSE_LINUX}),
    ('Linux', 'arm64'): ('7z2605-linux-arm64.tar.xz',
        '0b71f6cfc34669b3bf69f2347efd3429f77020665003a13b9cdc53b784270421',
        {'7zzs': '5d5104b7728ffb5395d2a7ae5e4e8f09f875bb776e19322f2df0d6167aa502fb', 'License.txt': LICENSE_LINUX}),
    ('Windows', 'x86_64'): ('7z2605-x64.exe',
        'fd99e86f57d8f429ef5ed045bf98315af962e2309b0f1ba87a147a44c71647d0',
        {'7z.exe': '94205cfc054dd3bbeb978905bc619a65b3f04e9e73bb2717841110b6f46d962e',
         '7z.dll': '78bf6b668d75801e3aa180b4ccb16e6d8395fba80dec8a065055bb6f8dfabb82', 'License.txt': LICENSE_WINDOWS}),
    ('Windows', 'arm64'): ('7z2605-arm64.exe',
        '86d5790d5dd84cab4636c3bb7fab10d3ed7cd3e57e6119401bd2769e28165268',
        {'7z.exe': '08c3868b294fd12984bd1537a939ae7200ad695420dc65a2eeeec9f8fdf99772',
         '7z.dll': '311d0a45182401e10c71a12b33a5c4a7171f6a8c1d67eeec9ef5dc2a5c962fe8', 'License.txt': LICENSE_WINDOWS}),
}


def ensure(ok, message):
    if not ok:
        raise ValueError(message)


def host():
    system, machine = platform.system(), platform.machine().lower()
    arch = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(machine)
    ensure((system, arch) in IPSW and struct.calcsize('P') == 8,
           'Native firmware preparation supports 64-bit Windows/Linux on x86-64 or ARM64.')
    return system, arch


def run_capture(args, timeout=300, limit=4 * 1024 * 1024):
    """Bound native helper time and output on Windows as well as POSIX hosts."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([str(v) for v in args], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                ensure(time.monotonic() < deadline, 'Native extraction helper timed out.')
                ensure(os.fstat(output.fileno()).st_size <= limit, 'Native extraction helper output exceeded its limit.')
                time.sleep(0.05)
            ensure(os.fstat(output.fileno()).st_size <= limit, 'Native extraction helper output exceeded its limit.')
            output.seek(0)
            result = output.read(limit + 1)
            ensure(process.returncode == 0,
                   'Native extraction helper failed: ' + result.decode('utf-8', errors='replace')[-3000:])
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def archive_files(data, names, kind):
    """Read only named regular members; never extract archive paths to disk."""
    result = {}
    if kind == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                name = PurePosixPath(item.filename).name
                if name not in names:
                    continue
                ensure(name not in result and not item.is_dir() and not stat.S_ISLNK(item.external_attr >> 16)
                       and not item.flag_bits & 1 and item.file_size <= 200 * 1024 * 1024,
                       'Unexpected native helper ZIP member.')
                result[name] = archive.read(item)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            for item in archive.getmembers():
                name = PurePosixPath(item.name).name
                if name not in names:
                    continue
                ensure(name not in result and item.isfile() and item.size <= 200 * 1024 * 1024,
                       'Unexpected native helper TAR member.')
                with archive.extractfile(item) as source:
                    result[name] = source.read()
    ensure(set(result) == set(names), 'Native helper archive is missing a required file.')
    return result


def cleanup_helper(temporary, system):
    # Windows can briefly retain an executable mapping after its process exits
    # (including under x64/x86 emulation). Retry only deletion of our private
    # temporary helper directory, with a five-second upper bound.
    for attempt in range(51):
        try:
            temporary.cleanup()
            return
        except PermissionError:
            if system != 'Windows' or attempt == 50:
                raise
            time.sleep(0.1)


def prepare_tool(root, fetch, kind):
    system, arch = host()
    config, version, base = (IPSW, IPSW_VERSION, IPSW_BASE) if kind == 'ipsw' else (SEVENZIP, SEVENZIP_VERSION, SEVENZIP_BASE)
    ensure(kind in {'ipsw', '7zip'}, 'Unknown firmware helper.')
    asset, archive_sha, hashes = config[(system, arch)]
    folder = data_root(root) / '.carrier-tools' / (kind + '-' + version + '-' + system.lower() + '-' + arch)
    ensure(not folder.is_symlink(), 'Refusing a symlink at the native helper directory.')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    executable = folder / ('ipsw.exe' if system == 'Windows' else 'ipsw') if kind == 'ipsw' else folder / ('7z.exe' if system == 'Windows' else '7zzs')
    missing = []
    for name, expected in hashes.items():
        path = folder / name
        ensure(not path.is_symlink(), 'Refusing a symlink at the native helper path.')
        if path.exists():
            ensure(path.is_file() and path.stat().st_size <= 200 * 1024 * 1024
                   and store.digest(path.read_bytes()) == expected, 'Cached native helper checksum mismatch: ' + name)
        else:
            missing.append(name)
    if not missing:
        return executable
    print('Downloading pinned ' + kind + ' helper for ' + system + ' / ' + arch + '...', flush=True)
    data = fetch(base + asset, 64 * 1024 * 1024)
    ensure(store.digest(data) == archive_sha, 'Native helper archive checksum mismatch.')
    if kind == '7zip' and system == 'Windows':
        # Read the pinned installer as a 7z archive. Never execute the
        # installer, register shell extensions, alter PATH, or ask for admin.
        temporary = tempfile.TemporaryDirectory(prefix='.unpack-', dir=folder)
        try:
            work = Path(temporary.name)
            bootstrap = fetch(SEVENZIP_BASE + '7zr.exe', 2 * 1024 * 1024)
            ensure(store.digest(bootstrap) == BOOTSTRAP_SHA256, '7-Zip bootstrap checksum mismatch.')
            (work / '7zr.exe').write_bytes(bootstrap)
            (work / 'package.exe').write_bytes(data)
            destination = work / 'files'
            run_capture([work / '7zr.exe', 'e', '-t7z', '-y', '-bd', '-bso0', '-bsp0',
                         '-o' + str(destination), work / 'package.exe', *hashes])
            ensure({p.name for p in destination.iterdir()} == set(hashes), 'Unexpected files in the native helper package.')
            files = {name: (destination / name).read_bytes() for name in hashes}
        finally:
            cleanup_helper(temporary, system)
    else:
        files = archive_files(data, hashes, 'zip' if asset.endswith('.zip') else 'tar')
    for name, expected in hashes.items():
        ensure(store.digest(files[name]) == expected, 'Native helper executable checksum mismatch: ' + name)
    for name in missing:
        store.atomic_write(folder / name, files[name])
        (folder / name).chmod(0o700 if name != 'License.txt' else 0o600)
    return executable


def safe_path(value):
    value = value.replace('\\', '/')
    parts = value.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    ensure(len(value) <= 1024 and all(p not in {'', '.', '..'} and len(p) <= 200
           and re.fullmatch(r'[A-Za-z0-9_.+ -]+', p) and p == p.rstrip('. ')
           and p.split('.')[0].upper() not in reserved for p in parts),
           'Unsupported or unsafe APFS member path: ' + repr(value[:200]))
    return '/'.join(parts)


def select_members(listing, names):
    """Select one complete pair from one volume, with bounded regular files."""
    records = []
    current = {}
    for line in listing.replace('\r\n', '\n').split('\n') + ['']:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        ensure(' = ' in line, 'Unrecognized APFS listing format.')
        key, value = line.split(' = ', 1)
        ensure(key not in current, 'Duplicate APFS listing field.')
        current[key] = value
    ensure(0 < len(records) <= 512, 'APFS carrier listing is empty or exceeds its scope.')
    selected, roots, totals = {}, {t: set() for t in names}, {t: 0 for t in names}
    for record in records:
        # Extended attributes are metadata used by 7-Zip for decompression;
        # they are not separate carrier files and must not become NTFS streams.
        if record.get('Alternate Stream') == '+':
            continue
        path = safe_path(record.get('Path', ''))
        matches = []
        for target, bundle in names.items():
            marker = 'System/Library/Carrier Bundles/iPhone/' + bundle + '.bundle'
            start = ('/' + path).find('/' + marker)
            if start >= 0 and (path[start + len(marker):] == '' or path[start + len(marker):].startswith('/')):
                root = path[:start + len(marker)]
                matches.append((target, root, path[len(root):].lstrip('/')))
        ensure(len(matches) == 1, 'APFS reader returned a file outside the requested carrier pair.')
        target, root, relative = matches[0]
        roots[target].add(root)
        ensure(not record.get('Symbolic Link') and not record.get('Hard Link'),
               'APFS carrier links are not supported.')
        # APFS exposes kpidPosixAttrib (Mode), not the Folder property that
        # several other 7-Zip formats publish. Require an actual regular file
        # or directory; never infer the type from its name or size.
        mode = record.get('Mode', '')
        ensure(re.fullmatch(r'[d-][rwxstST-]{9}', mode),
               'APFS member has an unsupported POSIX mode: ' + repr(mode) + ' at ' + path)
        is_directory = mode[0] == 'd'
        ensure(record.get('Folder', '+' if is_directory else '-') == ('+' if is_directory else '-'),
               'Inconsistent APFS file type.')
        if is_directory:
            ensure(relative in {'', 'signatures'}, 'Unmodeled nested carrier directory.')
            continue
        ensure(relative and path not in selected, 'Duplicate or missing APFS carrier filename.')
        classify(relative)
        try:
            size = int(record.get('Size', ''))
        except ValueError as error:
            raise ValueError('Missing APFS member size.') from error
        ensure(0 <= size <= MAX_BYTES, 'APFS carrier member exceeds its size limit.')
        totals[target] += size
        ensure(totals[target] <= MAX_BYTES, 'APFS carrier bundle exceeds its size limit.')
        selected[path] = (target, relative, size)
    ensure(all(len(v) == 1 for v in roots.values()), 'Expected exactly one APFS root for each carrier bundle.')
    ensure(len({next(iter(v)).rsplit('/', 1)[0] for v in roots.values()}) == 1,
           'Carrier pair must come from the same APFS volume and directory.')
    for target in names:
        files = [relative for t, relative, _ in selected.values() if t == target]
        ensure(0 < len(files) <= 100 and {'Info.plist', 'carrier.plist'} <= set(files),
               'Incomplete or oversized APFS carrier bundle.')
    return selected


def extract(image, work, tool, names):
    image, work = Path(image), Path(work)
    with image.open('rb') as source:
        header = source.read(36)
    ensure(header[32:36] == b'NXSB',
           'Native extraction expects a raw APFS image. This firmware image format needs an app update.')
    includes = []
    for bundle in names.values():
        path = 'System/Library/Carrier Bundles/iPhone/' + bundle + '.bundle'
        includes += ['-ir!' + prefix + path + suffix for prefix in ('', '*/') for suffix in ('', '/*')]
    print('Reading the two carrier directories directly from APFS; no filesystem mount...', flush=True)
    listing = run_capture([tool, 'l', '-tAPFS', '-slt', '-ba', '-sccUTF-8', image, *includes]).decode('utf-8')
    selected = select_members(listing, names)
    members = work / 'apfs-members.txt'
    members.write_text(''.join(path + '\n' for path in selected), encoding='utf-8', newline='\n')
    output = work / 'extracted'
    output.mkdir()
    try:
        run_capture([tool, 'x', '-tAPFS', '-y', '-bd', '-bso0', '-bsp0', '-sns-', '-spd', '-scsUTF-8',
                     '-o' + str(output), image, '@' + str(members)])
        found = {}
        for directory, dirs, files in os.walk(output, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                info = path.lstat()
                ensure(not stat.S_ISLNK(info.st_mode)
                       and not getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0),
                       'Native extraction produced a link or reparse point.')
                if name in files:
                    ensure(stat.S_ISREG(info.st_mode), 'Native extraction produced a non-regular file.')
                    relative = path.relative_to(output).as_posix()
                    ensure(relative in selected and info.st_size == selected[relative][2],
                           'Native extraction produced unexpected files or sizes.')
                    found[relative] = path
        ensure(set(found) == set(selected), 'Native extraction did not produce every selected carrier file.')
        bundles = {target: {} for target in names}
        for path, (target, relative, _) in selected.items():
            bundles[target][relative] = found[path].read_bytes()
        return bundles
    finally:
        # APFS read-only attributes must not prevent deletion on Windows.
        for directory, dirs, files in os.walk(output, followlinks=False):
            def regular_path(path):
                info = path.lstat()
                return not stat.S_ISLNK(info.st_mode) and not (
                    getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
            dirs[:] = [name for name in dirs if regular_path(Path(directory) / name)]
            for name in files:
                path = Path(directory) / name
                if regular_path(path) and stat.S_ISREG(path.lstat().st_mode):
                    path.chmod(0o600)
