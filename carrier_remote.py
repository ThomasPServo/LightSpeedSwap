"""Bounded HTTP ranges and resumable downloads of one Apple IPSW member.

ZIP64 parsing, decompression, and complete member CRC checks use zipfile. The
optional checkpoint stores only that member's compressed bytes, never an IPSW.
"""
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from carrier_https import https_context
import carrier_store as store

READ_LIMIT = 16 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
RANGE_ATTEMPTS = 3
DOWNLOAD_SCHEMA = 'lightspeedswap.firmware-download.v1'


class DownloadInterrupted(ValueError):
    """A transient network failure exhausted the bounded per-range retries."""


def download_paths(folder):
    folder = Path(folder)
    return folder / 'firmware-download.part', folder / 'firmware-download.json'


def _read_checkpoint(part, record):
    for path in (part, record):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Refusing an unsafe firmware download checkpoint: ' + str(path))
    if not record.exists():
        if part.exists():
            raise ValueError('Firmware download checkpoint is missing its receipt. '
                             'Run: cache clear-download, then setup-device.')
        return None
    with record.open('rb') as source:
        data = source.read(16385)
    if len(data) > 16384:
        raise ValueError('Firmware download receipt is too large. Run: cache clear-download.')
    try:
        saved = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid firmware download receipt. Run: cache clear-download.') from exc
    if not isinstance(saved, dict) or saved.get('schema') != DOWNLOAD_SCHEMA:
        raise ValueError('Unknown firmware download receipt. Run: cache clear-download.')
    return saved


def clear_download(folder):
    """Remove only the named download files, never bundles, receipts, or mounts."""
    part, record = download_paths(folder)
    with store.lock(part):
        for path in (part, record):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError('Refusing an unsafe firmware download checkpoint: ' + str(path))
        part.unlink(missing_ok=True)
        record.unlink(missing_ok=True)


class RangeReader(io.RawIOBase):
    def __init__(self, url, timeout=45):
        super().__init__()
        self.url, self.position, self.size = url, 0, None
        # This is a socket inactivity timeout PER request, not a deadline for
        # the entire multi-GB download. Steady progress can take over 45 minutes.
        self.timeout = timeout
        self.cache = None
        self._range(0, 0)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        value = offset + (0 if whence == 0 else self.position if whence == 1 else self.size if whence == 2 else -10**30)
        if not 0 <= value <= self.size:
            raise ValueError('IPSW reader received an invalid seek.')
        self.position = value
        return value

    def _range(self, start, end):
        for attempt in range(RANGE_ATTEMPTS):
            try:
                return self._request_range(start, end)
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                exc.close()
            except urllib.error.URLError as exc:
                # Never retry a certificate failure with weaker TLS settings.
                if isinstance(exc.reason, ssl.SSLCertVerificationError) or not isinstance(exc.reason, OSError):
                    raise
            except (TimeoutError, ConnectionError, http.client.IncompleteRead):
                pass
            if attempt + 1 < RANGE_ATTEMPTS:
                print('Firmware connection interrupted; retrying the same range '
                      f'({attempt + 2}/{RANGE_ATTEMPTS})...', flush=True)
                time.sleep(attempt + 1)
        raise DownloadInterrupted('Firmware connection failed after three attempts. '
                                  'Run setup-device again to resume any saved download. '
                                  'No phone operation was attempted.')

    def _request_range(self, start, end):
        request = urllib.request.Request(self.url, headers={
            'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
            'User-Agent': 'LightSpeedSwap/0.7.3'})
        with urllib.request.urlopen(request, timeout=self.timeout, context=https_context()) as response:
            final = urllib.parse.urlsplit(response.geturl())
            host = final.hostname or ''
            if (final.scheme != 'https' or not any(host == domain or host.endswith('.' + domain)
                                                  for domain in ('apple.com', 'cdn-apple.com'))):
                raise ValueError('Firmware range request redirected outside Apple HTTPS.')
            header = response.headers.get('Content-Range', '')
            match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+)', header)
            if (response.status != 206 or not match or int(match[1]) != start or int(match[2]) != end
                    or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise ValueError('The firmware server did not honor the exact HTTP range. No full-download fallback.')
            total = int(match[3])
            if total <= end or (self.size is not None and self.size != total):
                raise ValueError('The firmware archive changed size during download.')
            data = response.read(end - start + 2)
        if len(data) != end - start + 1:
            raise http.client.IncompleteRead(data, end - start + 1 - len(data))
        self.size = total
        return data

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        if size > READ_LIMIT:
            raise ValueError('Unsupported IPSW structure: a read exceeds the bounded memory limit.')
        if size == 0:
            return b''
        start = self.position
        data = b''
        cache = self.cache
        # zipfile opens/validates the fresh local header before caching begins.
        # Only the selected member's compressed extent can enter the checkpoint.
        if cache and cache['start'] <= start and start + size <= cache['end']:
            offset = start - cache['start']
            source = cache['file']
            if offset < cache['bytes']:
                source.seek(offset)
                data = source.read(min(size, cache['bytes'] - offset))
                if len(data) != min(size, cache['bytes'] - offset):
                    raise ValueError('Firmware download checkpoint was truncated during use.')
            if len(data) < size:
                chunk = self._range(start + len(data), start + size - 1)
                if offset + len(data) == cache['bytes']:
                    source.seek(cache['bytes'])
                    source.write(chunk)
                    source.flush()
                    cache['bytes'] += len(chunk)
                data += chunk
        else:
            data = self._range(start, start + size - 1)
        self.position += len(data)
        return data

    @contextmanager
    def checkpoint(self, folder, member):
        if folder is None:
            yield 0
            return
        folder = Path(folder)
        if folder.is_symlink():
            raise ValueError('Refusing a symlink at the firmware download folder.')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        part, record = download_paths(folder)
        expected = {'schema': DOWNLOAD_SCHEMA, 'url': self.url, 'archive_bytes': self.size,
                    'member': member.filename, 'start': self.position, 'compressed_bytes': member.compress_size,
                    'bytes': member.file_size, 'crc32': member.CRC, 'compression': member.compress_type}
        with store.lock(part):
            saved = _read_checkpoint(part, record)
            if saved is not None and saved != expected:
                # One slot only. A different image cannot inherit old bytes.
                print('Firmware source changed; replacing the unfinished download.', flush=True)
                part.unlink(missing_ok=True)
                record.unlink()
            if part.exists() and part.stat().st_size > member.compress_size:
                raise ValueError('Firmware download checkpoint is oversized. Run: cache clear-download.')
            store.atomic_write(record, json.dumps(expected, sort_keys=True).encode())
            fd = os.open(part, os.O_RDWR | os.O_CREAT, 0o600)
            corrupt = False
            try:
                with os.fdopen(fd, 'r+b') as output:
                    completed = output.seek(0, os.SEEK_END)
                    self.cache = {'file': output, 'bytes': completed, 'start': self.position,
                                  'end': self.position + member.compress_size}
                    if completed:
                        print(f'Resuming firmware download: {100 * completed / max(1, member.compress_size):.1f}% '
                              'already saved. Rechecking saved bytes locally...', flush=True)
                    try:
                        yield completed
                    except (zipfile.BadZipFile, zlib.error, EOFError):
                        corrupt = True
                        raise
                    finally:
                        self.cache = None
            except BaseException:
                if corrupt:
                    part.unlink(missing_ok=True)
                    record.unlink(missing_ok=True)
                elif part.exists():
                    print('Unfinished firmware download kept at ' + str(part)
                          + '. Run setup-device again to resume, or cache clear-download to discard.', flush=True)
                raise
            else:
                part.unlink()
                record.unlink()


class RemoteFirmware:
    def __init__(self, url):
        self.reader = RangeReader(url)
        try:
            self.zip = zipfile.ZipFile(self.reader)
        except BaseException:
            self.reader.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.zip.close()
        self.reader.close()

    def member(self, name, limit):
        matches = [i for i in self.zip.infolist() if i.filename == name]
        if len(matches) != 1:
            raise ValueError('Firmware must contain exactly one ' + name)
        member = matches[0]
        if (member.is_dir() or member.flag_bits & 1 or not 0 < member.file_size <= limit
                or not 0 < member.compress_size <= limit
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise ValueError('Unsupported firmware ZIP member format or size.')
        return member

    def manifest(self):
        member = self.member('BuildManifest.plist', 10 * 1024 * 1024)
        return self.zip.read(member)

    def download_image(self, name, destination, resume_dir=None):
        member = self.member(name, 64 * 1024**3)
        destination = Path(destination)
        sha, completed, last_report = hashlib.sha256(), 0, 0
        with self.zip.open(member) as source, self.reader.checkpoint(resume_dir, member) as saved:
            # While downloading, compressed checkpoint and expanded output coexist.
            # The checkpoint is deleted before AEA decryption needs a second image.
            peak = member.file_size * (2 if name.endswith('.aea') else 1)
            if resume_dir is not None:
                peak = max(peak, member.file_size + member.compress_size) - saved
            needed = peak + 3 * 1024**3
            if shutil.disk_usage(destination.parent).free < needed:
                raise ValueError(f'This firmware needs at least {needed / 1024**3:.1f} GiB free temporary space.')
            with destination.open('xb') as output:
                while True:
                    data = source.read(CHUNK)
                    if not data:
                        break
                    output.write(data)
                    sha.update(data)
                    completed += len(data)
                    if time.monotonic() - last_report >= 10:
                        print(f'Firmware image: {100 * completed / member.file_size:.1f}%', flush=True)
                        last_report = time.monotonic()
            if completed != member.file_size:
                raise ValueError('Incomplete firmware image; no payload cached.')
        # Reading through EOF verified CRC over ALL bytes, including a saved prefix.
        return {'bytes': completed, 'sha256': sha.hexdigest(), 'crc32': f'{member.CRC:08x}'}
