"""Bounded HTTP-range access to one member of an official Apple IPSW ZIP.

ZIP64 parsing and member CRC validation are handled by Python's zipfile.
This downloads the selected filesystem image, not sparse files inside APFS.
"""
import hashlib
import io
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from carrier_https import https_context

READ_LIMIT = 16 * 1024 * 1024
CHUNK = 4 * 1024 * 1024


class RangeReader(io.RawIOBase):
    def __init__(self, url, timeout=2700):
        super().__init__()
        self.url, self.position, self.size = url, 0, None
        self.deadline = time.monotonic() + timeout
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
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Firmware download timed out. No phone operation was attempted.')
        request = urllib.request.Request(self.url, headers={
            'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
            'User-Agent': 'LightSpeedSwap/0.5.0'})
        with urllib.request.urlopen(request, timeout=min(45, remaining), context=https_context()) as response:
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
            self.size = total
            data = response.read(end - start + 2)
        if len(data) != end - start + 1:
            raise ValueError('Incomplete firmware range response.')
        return data

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        if size > READ_LIMIT:
            raise ValueError('Unsupported IPSW structure: a read exceeds the bounded memory limit.')
        if size == 0:
            return b''
        data = self._range(self.position, self.position + size - 1)
        self.position += size
        return data


class RemoteFirmware:
    def __init__(self, url):
        self.reader = RangeReader(url)
        self.zip = zipfile.ZipFile(self.reader)

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
                or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
            raise ValueError('Unsupported firmware ZIP member format or size.')
        return member

    def manifest(self):
        member = self.member('BuildManifest.plist', 10 * 1024 * 1024)
        return self.zip.read(member)

    def download_image(self, name, destination):
        member = self.member(name, 64 * 1024**3)
        destination = Path(destination)
        needed = member.file_size * (2 if name.endswith('.aea') else 1) + 3 * 1024**3
        if shutil.disk_usage(destination.parent).free < needed:
            raise ValueError(f'This firmware needs at least {needed / 1024**3:.1f} GiB free temporary space.')
        sha, completed, last_report = hashlib.sha256(), 0, 0
        with self.zip.open(member) as source, destination.open('xb') as output:
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
        # Reading through EOF verified the complete ZIP member CRC32.
        return {'bytes': completed, 'sha256': sha.hexdigest(), 'crc32': f'{member.CRC:08x}'}
