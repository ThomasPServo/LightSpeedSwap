"""Real ZIP/ZIP64 stream handling through controlled HTTP-range responses."""
import contextlib
import hashlib
import io
from pathlib import Path
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import carrier_remote as remote

URL = 'https://updates.cdn-apple.com/fixture.ipsw'


class Response(io.BytesIO):
    def __init__(self, data, start, end, total):
        super().__init__(data)
        self.status = 206
        self.headers = {'Content-Range': f'bytes {start}-{end}/{total}'}
    def geturl(self):
        return URL


def firmware(compression):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression) as archive:
        archive.writestr('BuildManifest.plist', b'manifest fixture')
        with archive.open('image.dmg.aea', 'w', force_zip64=True) as output:
            output.write(b'bounded image bytes' * 10000)
    return out.getvalue()


class RangeDownloadContracts(unittest.TestCase):
    def serve(self, data):
        self.requests = []
        def fetch(request, timeout):
            match = re.fullmatch(r'bytes=([0-9]+)-([0-9]+)', request.get_header('Range'))
            self.assertIsNotNone(match)
            start, end = map(int, match.groups())
            self.requests.append((start, end))
            self.assertLessEqual(end - start + 1, remote.READ_LIMIT)
            return Response(data[start:end + 1], start, end, len(data))
        return patch.object(remote.urllib.request, 'urlopen', side_effect=fetch)

    def test_stored_and_deflated_zip64_members_stream_with_crc(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression), self.serve(firmware(compression)), \
                 tempfile.TemporaryDirectory() as tmp, patch.object(remote, 'CHUNK', 1024), \
                 contextlib.redirect_stdout(io.StringIO()):
                with remote.RemoteFirmware(URL) as archive:
                    self.assertEqual(archive.manifest(), b'manifest fixture')
                    path = Path(tmp) / 'image.dmg.aea'
                    receipt = archive.download_image(path.name, path)
                    expected = b'bounded image bytes' * 10000
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertEqual(receipt['sha256'], hashlib.sha256(expected).hexdigest())
                    self.assertEqual(receipt['bytes'], len(expected))

    def test_full_response_wrong_range_and_short_range_are_rejected(self):
        for mode in ('full', 'wrong-range', 'short', 'encoding', 'external'):
            def fetch(*args, **kwargs):
                value = Response(b'x', 0, 0, 500)
                if mode == 'full': value.status = 200
                if mode == 'wrong-range': value.headers['Content-Range'] = 'bytes 1-1/500'
                if mode == 'short': value = Response(b'', 0, 0, 500)
                if mode == 'encoding': value.headers['Content-Encoding'] = 'gzip'
                if mode == 'external': value.geturl = lambda: 'https://example.org/a.ipsw'
                return value
            with self.subTest(mode=mode), patch.object(remote.urllib.request, 'urlopen', side_effect=fetch), \
                 self.assertRaises(ValueError):
                remote.RangeReader(URL)

    def test_large_read_does_not_fall_back_to_whole_archive(self):
        with patch.object(remote.urllib.request, 'urlopen', return_value=Response(b'x', 0, 0, 10**10)) as fetch:
            reader = remote.RangeReader(URL)
            with self.assertRaisesRegex(ValueError, 'memory limit'):
                reader.read()
            self.assertEqual(fetch.call_count, 1)

    def test_corrupt_image_crc_stops_before_any_cache(self):
        data = bytearray(firmware(zipfile.ZIP_STORED))
        data[data.index(b'bounded image bytes')] ^= 1
        with self.serve(data), tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            with remote.RemoteFirmware(URL) as archive, self.assertRaisesRegex(zipfile.BadZipFile, 'CRC'):
                archive.download_image('image.dmg.aea', Path(tmp) / 'image.dmg.aea')

    def test_disk_budget_uses_actual_member_size(self):
        with self.serve(firmware(zipfile.ZIP_STORED)), tempfile.TemporaryDirectory() as tmp, \
             patch.object(remote.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024)):
            with remote.RemoteFirmware(URL) as archive, self.assertRaisesRegex(ValueError, 'temporary space'):
                archive.download_image('image.dmg.aea', Path(tmp) / 'image.dmg.aea')
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_archive_size_change_is_rejected(self):
        replies = [Response(b'x', 0, 0, 500), Response(b'x', 0, 0, 501)]
        with patch.object(remote.urllib.request, 'urlopen', side_effect=replies):
            reader = remote.RangeReader(URL)
            with self.assertRaisesRegex(ValueError, 'changed size'):
                reader.read(1)


if __name__ == '__main__':
    unittest.main()
