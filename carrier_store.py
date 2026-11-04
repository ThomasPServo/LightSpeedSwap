"""A bounded compressed archive of content-addressed carrier files.

No filesystem extraction: archive paths are identifiers, never output paths.
Only verified payloads are submitted by carrier_payloads; reads verify SHA-256.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_ENTRIES = 8
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_OBJECT_BYTES = 10 * 1024 * 1024
MAX_MEMBERS = 4096
HEX = re.compile(r'[0-9a-f]{64}')


def ensure(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entry_key(device):
    return digest(json.dumps(device, sort_keys=True, separators=(',', ':')).encode())


def referenced(entry):
    return {entry['manifest']} | {v for b in entry['bundles'].values() for v in b['files'].values()}


def compact(entries, objects):
    needed = set().union(*(referenced(e) for e in entries.values())) if entries else set()
    ensure(needed <= set(objects), 'The bundle store is missing referenced file bytes.')
    return {k: objects[k] for k in sorted(needed)}


def encode(entries, objects):
    objects = compact(entries, objects)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr('index.json', json.dumps({'schema': 1, 'entries': entries}, sort_keys=True, separators=(',', ':')))
        for key, data in objects.items():
            ensure(digest(data) == key, 'Object bytes do not match their digest.')
            ensure(len(data) <= MAX_OBJECT_BYTES, 'A carrier object exceeds the size limit.')
            z.writestr('objects/' + key, data)
    ensure(sum(map(len, objects.values())) <= MAX_UNPACKED_BYTES, 'Bundle store exceeds the unpacked size limit.')
    return out.getvalue()


def decode(data):
    ensure(len(data) <= MAX_CACHE_BYTES, 'Bundle pack exceeds the 8 MiB size limit.')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = z.infolist()
        names = [m.filename for m in members]
        ensure(len(members) <= MAX_MEMBERS and len(names) == len(set(names)), 'Invalid or duplicate bundle pack members.')
        ensure(sum(m.file_size for m in members) <= MAX_UNPACKED_BYTES, 'Bundle pack expands beyond the size limit.')
        for m in members:
            ensure(not m.is_dir() and not stat.S_ISLNK(m.external_attr >> 16)
                   and m.file_size <= MAX_OBJECT_BYTES and not (m.flag_bits & 1), 'Unsupported bundle pack member.')
            ensure(m.filename == 'index.json' or re.fullmatch(r'objects/[0-9a-f]{64}', m.filename),
                   'Unexpected bundle pack member path.')
        ensure('index.json' in names and z.getinfo('index.json').file_size <= 512 * 1024, 'Missing or oversized bundle index.')
        def unique_object(pairs):
            result = {}
            for name, value in pairs:
                ensure(name not in result, 'Duplicate key in bundle pack index.')
                result[name] = value
            return result
        index = json.loads(z.read('index.json'), object_pairs_hook=unique_object)
        ensure(isinstance(index, dict) and type(index.get('schema')) is int
               and index['schema'] == 1 and isinstance(index.get('entries'), dict),
               'Unsupported bundle pack schema. A newer format requires an app update.')
        entries = index['entries']
        ensure(0 < len(entries) <= MAX_CACHE_ENTRIES, 'Unexpected number of bundle pack entries.')
        objects = {n[8:]: z.read(n) for n in names if n.startswith('objects/')}
    for key, value in objects.items():
        ensure(digest(value) == key, 'Bundle object changed: digest mismatch.')
    for key, e in entries.items():
        ensure(isinstance(e, dict) and isinstance(e.get('device'), dict)
               and e['device'] and all(isinstance(v, str) for v in e['device'].values()),
               'Invalid bundle entry device metadata.')
        ensure(HEX.fullmatch(key) is not None and key == entry_key(e['device']), 'Bundle entry identity changed.')
        ensure(type(e.get('sequence')) is int and e['sequence'] >= 0, 'Invalid cache sequence.')
        ensure(isinstance(e.get('manifest'), str) and HEX.fullmatch(e['manifest']), 'Invalid manifest reference.')
        ensure(isinstance(e.get('bundles'), dict) and set(e['bundles']) == {'mvno', 'wholesale'},
               'Bundle pack must contain both switching directions.')
        for bundle in e['bundles'].values():
            ensure(isinstance(bundle, dict) and isinstance(bundle.get('files'), dict) and bundle['files']
                   and all(isinstance(n, str) and n and isinstance(v, str) and HEX.fullmatch(v)
                           for n, v in bundle['files'].items()), 'Invalid bundle file references.')
        ensure(all(isinstance(k, str) and HEX.fullmatch(k) for k in referenced(e)), 'Invalid object reference.')
    ensure(set(compact(entries, objects)) == set(objects), 'Bundle pack contains unreferenced objects.')
    return entries, objects


def read(path):
    path = Path(path)
    ensure(not path.is_symlink(), 'Refusing a symlink at the bundle store path.')
    ensure(path.stat().st_size <= MAX_CACHE_BYTES, 'Bundle store exceeds the 8 MiB size limit.')
    return decode(path.read_bytes())


def atomic_write(path, data):
    path = Path(path)
    ensure(not path.is_symlink(), 'Refusing to replace a symlink at the bundle store path.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.payload-write-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = path.with_suffix('.lock')
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Cache is being written by another process, or an interrupted writer left ' + str(marker)
                         + '. Close other app processes before removing a stale lock.') from exc
    os.close(fd)
    try:
        yield
    finally:
        marker.unlink()


def put(path, entry, incoming_objects, validate_existing=None):
    """Atomic merge; evict oldest prepared entries, preserve the new entry."""
    path = Path(path)
    key = entry_key(entry['device'])
    with lock(path):
        entries, objects = read(path) if path.exists() else ({}, {})
        # A payload upgrade must compare against the current entry while holding
        # the writer lock, not a snapshot taken before a long firmware download.
        if validate_existing is not None and key in entries:
            validate_existing(entries[key], objects)
        entry = dict(entry, sequence=max((e['sequence'] for e in entries.values()), default=0) + 1)
        entries[key] = entry
        objects.update(incoming_objects)
        evicted = 0
        while True:
            objects = compact(entries, objects)
            fits_count = len(entries) <= MAX_CACHE_ENTRIES
            fits_unpacked = sum(map(len, objects.values())) <= MAX_UNPACKED_BYTES
            encoded = encode(entries, objects) if fits_count and fits_unpacked else None
            if encoded is not None and len(encoded) <= MAX_CACHE_BYTES:
                break
            victims = [k for k in entries if k != key]
            ensure(victims, 'This payload pair is larger than the 8 MiB cache budget. Existing cache preserved.')
            del entries[min(victims, key=lambda k: entries[k]['sequence'])]
            evicted += 1
        # Roundtrip before committing anything to disk.
        decode(encoded)
        atomic_write(path, encoded)
    return {'bytes': len(encoded), 'entries': len(entries), 'objects': len(objects), 'evicted_entries': evicted}


def export_entry(source, key, destination):
    entries, objects = source
    ensure(key in entries, 'No matching payload to export.')
    data = encode({key: entries[key]}, objects)
    decode(data)
    with Path(destination).open('xb') as output:
        output.write(data)
    return len(data)


def statistics(path):
    path = Path(path)
    if not path.exists():
        return {'bytes': 0, 'entries': 0, 'unique_objects': 0, 'limit_bytes': MAX_CACHE_BYTES, 'limit_entries': MAX_CACHE_ENTRIES}
    entries, objects = read(path)
    references = sum(len(referenced(e)) for e in entries.values())
    return {'bytes': path.stat().st_size, 'entries': len(entries), 'unique_objects': len(objects),
            'duplicate_references_reused': references - len(objects),
            'limit_bytes': MAX_CACHE_BYTES, 'limit_entries': MAX_CACHE_ENTRIES,
            'devices': [e['device'] for e in sorted(entries.values(), key=lambda e: e['sequence'], reverse=True)]}


def clear(path):
    path = Path(path)
    with lock(path):
        ensure(not path.is_symlink(), 'Refusing a symlink at the cache path.')
        path.unlink(missing_ok=True)
