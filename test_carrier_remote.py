"""Real ZIP/ZIP64 stream handling through controlled HTTP-range responses."""
import contextlib
import hashlib
import io
import json
import random
from pathlib import Path
import re
import tempfile
import unittest
import urllib.error
import ssl
import zlib
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


def urllib_request(value):
    return remote.urllib.request.Request(URL, headers={'Range': value})


def firmware(compression, payload=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression) as archive:
        archive.writestr('BuildManifest.plist', b'manifest fixture')
        with archive.open('image.dmg.aea', 'w', force_zip64=True) as output:
            output.write(b'bounded image bytes' * 10000 if payload is None else payload)
    return out.getvalue()


class RangeDownloadContracts(unittest.TestCase):
    def serve(self, data):
        self.requests = []
        def fetch(request, timeout, context):
            self.assertTrue(context.check_hostname)
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
                 patch.object(remote.time, 'sleep'), self.assertRaises(ValueError):
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


    def test_progress_can_continue_past_45_minutes(self):
        elapsed, timeouts = [0], []
        data = firmware(zipfile.ZIP_STORED)
        with self.serve(data) as fetch, tempfile.TemporaryDirectory() as tmp, \
             patch.object(remote, 'CHUNK', 4096), \
             patch.object(remote.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             contextlib.redirect_stdout(io.StringIO()):
            serve = fetch.side_effect
            def slow(*args, **kwargs):
                timeouts.append(kwargs['timeout'])
                result = serve(*args, **kwargs)
                elapsed[0] += 120  # Fast deterministic simulation of a slow connection.
                return result
            fetch.side_effect = slow
            with remote.RemoteFirmware(URL) as archive:
                result = archive.download_image('image.dmg.aea', Path(tmp) / 'image')
            self.assertGreater(elapsed[0], 2700)
            self.assertEqual(result['sha256'], hashlib.sha256(b'bounded image bytes' * 10000).hexdigest())
            self.assertEqual(set(timeouts), {45})

    def test_transient_errors_retry_same_range_but_invalid_responses_and_tls_do_not(self):
        data = firmware(zipfile.ZIP_STORED)
        for error in (TimeoutError('stalled'), ConnectionResetError('reset'),
                      urllib.error.URLError(TimeoutError('stalled')),
                      urllib.error.HTTPError(URL, 503, 'busy', {}, None)):
            with self.subTest(error=error), self.serve(data) as fetch, patch.object(remote.time, 'sleep'):
                serve = fetch.side_effect
                fetch.side_effect = [error, serve(urllib_request('bytes=0-0'), 45, remote.https_context())]
                reader = remote.RangeReader(URL)
                self.assertEqual(reader.size, len(data))
                self.assertEqual(fetch.call_count, 2)
                ranges = [c.args[0].get_header('Range') for c in fetch.call_args_list]
                self.assertEqual(ranges, ['bytes=0-0', 'bytes=0-0'])
        for error in (urllib.error.URLError(ssl.SSLCertVerificationError('untrusted')),
                      urllib.error.HTTPError(URL, 404, 'not found', {}, None),
                      ValueError('invalid range')):
            with self.subTest(error=error), patch.object(remote.urllib.request, 'urlopen', side_effect=error) as fetch, \
                 self.assertRaises(type(error)):
                remote.RangeReader(URL)
            self.assertEqual(fetch.call_count, 1)

    def interrupted_download(self, tmp, compression):
        payload = random.Random(47).randbytes(240000)
        data = firmware(compression, payload)
        with self.serve(data) as fetch, patch.object(remote, 'CHUNK', 16384), \
             patch.object(remote.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            serve = fetch.side_effect
            with remote.RemoteFirmware(URL) as archive:
                # Read the local ZIP header with zipfile, before enabling interruption.
                with archive.zip.open('image.dmg.aea'):
                    start = archive.reader.tell()
                stop = start + 3 * 16384
                end = start + archive.zip.getinfo('image.dmg.aea').compress_size
                def flaky(request, **kwargs):
                    first, last = map(int, request.get_header('Range')[6:].split('-'))
                    if stop <= first <= last < end and last - first < 16384:
                        raise TimeoutError('network disappeared')
                    return serve(request, **kwargs)
                fetch.side_effect = flaky
                with self.assertRaises(remote.DownloadInterrupted):
                    archive.download_image('image.dmg.aea', Path(tmp) / 'first', resume_dir=tmp)
                part, record = remote.download_paths(tmp)
                self.assertGreater(part.stat().st_size, 0)
                self.assertEqual(part.read_bytes(), data[start:start + part.stat().st_size])
                self.assertEqual(json.loads(record.read_text())['member'], 'image.dmg.aea')
                failed_ranges = [c.args[0].get_header('Range') for c in fetch.call_args_list[-3:]]
                self.assertEqual(len(set(failed_ranges)), 1)
        return data, payload, start, part.stat().st_size

    def test_stored_and_deflated_downloads_resume_without_redownloading_saved_prefix(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as tmp:
                data, payload, start, saved = self.interrupted_download(tmp, compression)
                with self.serve(data), patch.object(remote, 'CHUNK', 16384), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with remote.RemoteFirmware(URL) as archive:
                        result = archive.download_image('image.dmg.aea', Path(tmp) / 'second', resume_dir=tmp)
                    self.assertEqual((Path(tmp) / 'second').read_bytes(), payload)
                    self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
                    self.assertFalse(any(start <= a < start + saved for a, b in self.requests))
                    self.assertTrue(all(not p.exists() for p in remote.download_paths(tmp)))

    def test_corrupted_checkpoint_never_passes_crc_and_is_discarded(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as tmp:
                data, _, _, _ = self.interrupted_download(tmp, compression)
                part, record = remote.download_paths(tmp)
                changed = bytearray(part.read_bytes())
                changed[len(changed) // 2] ^= 1
                part.write_bytes(changed)
                with self.serve(data), contextlib.redirect_stdout(io.StringIO()):
                    with remote.RemoteFirmware(URL) as archive, self.assertRaises((zipfile.BadZipFile, zlib.error)):
                        archive.download_image('image.dmg.aea', Path(tmp) / 'second', resume_dir=tmp)
                self.assertFalse(part.exists())
                self.assertFalse(record.exists())

    def test_changed_image_replaces_checkpoint_and_explicit_clear_keeps_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, _, _ = self.interrupted_download(tmp, zipfile.ZIP_STORED)
            payload = random.Random(48).randbytes(240000)
            with self.serve(firmware(zipfile.ZIP_STORED, payload)), contextlib.redirect_stdout(io.StringIO()):
                with remote.RemoteFirmware(URL) as archive:
                    archive.download_image('image.dmg.aea', Path(tmp) / 'second', resume_dir=tmp)
            self.assertEqual((Path(tmp) / 'second').read_bytes(), payload)
            # Clear also recovers an orphaned/broken receipt, without touching other data.
            part, record = remote.download_paths(tmp)
            part.write_bytes(b'partial')
            record.write_text('broken receipt')
            remote.clear_download(tmp)
            self.assertEqual((Path(tmp) / 'second').read_bytes(), payload)
            self.assertFalse(part.exists())
            self.assertFalse(record.exists())


    def test_checkpoint_lock_prevents_a_second_writer_or_clear(self):
        with tempfile.TemporaryDirectory() as tmp, self.serve(firmware(zipfile.ZIP_STORED)):
            with remote.RemoteFirmware(URL) as archive, archive.zip.open('image.dmg.aea'):
                with archive.reader.checkpoint(tmp, archive.zip.getinfo('image.dmg.aea')):
                    with self.assertRaisesRegex(ValueError, 'another process'):
                        remote.clear_download(tmp)
                    part, _ = remote.download_paths(tmp)
                    self.assertTrue(part.exists())


if __name__ == '__main__':
    unittest.main()
