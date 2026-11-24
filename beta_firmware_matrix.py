#!/usr/bin/env python3
"""Audit every indexed iOS 27+ iPhone prerelease without accessing a phone.

Fetch public indexes once, replay their exact bytes through firmware_url,
and validate each Apple-hosted BuildManifest against every catalogued board.
Optional IPSW-range checks exercise the existing RemoteFirmware path as well.
This does not validate carrier extraction/signatures or physical activation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sys
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from unittest.mock import patch

import carrier_payloads as p
from carrier_remote import RemoteFirmware


def atomic_json(path, value):
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def fetch_json(url, folder):
    path = folder / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    if path.exists():
        return path.read_bytes()
    data = p.get_bytes(url, 32 * 1024 * 1024)
    json.loads(data)
    path.write_bytes(data)
    return data


def prereleases(catalog):
    result = []
    for item in catalog:
        match = re.fullmatch(r'([0-9]+(?:\.[0-9]+){0,3})(?: (?:beta|RC)(?: [0-9]+)?)?', item.get('version', ''))
        devices = [d for d in item.get('deviceMap', []) if re.fullmatch(r'iPhone[0-9]+,[0-9]+', d)]
        if (item.get('osStr') == 'iOS' and match and int(match.group(1).split('.')[0]) >= 27
                and (item.get('beta') or item.get('rc')) and devices):
            result.append({**item, '_version': match.group(1), '_devices': devices})
    return sorted(result, key=lambda item: (item.get('released', ''), item['build']))


def read_manifest(url, folder, archive=False):
    key = hashlib.sha256(url.encode()).hexdigest()
    suffix = '.archive.plist' if archive else '.plist'
    path = folder / (key + suffix)
    started = time.monotonic()
    if path.exists():
        data = path.read_bytes()
        source = 'cached IPSW member' if archive else 'cached Apple sidecar'
    elif archive:
        with RemoteFirmware(url) as firmware:
            data = firmware.manifest()
        path.write_bytes(data)
        source = 'IPSW member via RemoteFirmware'
    else:
        parts = urlsplit(url)
        sidecar = urlunsplit((parts.scheme, parts.netloc, parts.path.rsplit('/', 1)[0] + '/BuildManifest.plist', '', ''))
        try:
            data = p.get_bytes(sidecar, 16 * 1024 * 1024)
            plistlib.loads(data)
            source = 'Apple HTTPS BuildManifest sidecar'
        except (OSError, ValueError, plistlib.InvalidFileException):
            with RemoteFirmware(url) as firmware:
                data = firmware.manifest()
            source = 'IPSW member via RemoteFirmware (sidecar unavailable)'
        path.write_bytes(data)
    manifest = plistlib.loads(data)
    return {'url': url, 'source': source, 'file': str(path), 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'elapsed_seconds': round(time.monotonic() - started, 2)}, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--archive-checks', choices=['none', 'representative', 'all'], default='none')
    parser.add_argument('--reuse-snapshot', action='store_true',
                        help='Replay metadata/manifests already saved in this output directory')
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        parser.error('--workers must be between 1 and 32')
    out = args.output.absolute()
    if out.exists() and any(out.iterdir()) and not args.reuse_snapshot:
        parser.error('Use an empty output directory for fresh indexes, or --reuse-snapshot to replay saved evidence')
    out.mkdir(parents=True, exist_ok=True)
    metadata, manifests = out / 'metadata', out / 'manifests'
    metadata.mkdir(exist_ok=True)
    manifests.mkdir(exist_ok=True)
    catalog_path = out / 'ios-catalog.json'
    if not catalog_path.exists():
        catalog_path.write_bytes(fetch_json('https://api.appledb.dev/ios/iOS/main.json', metadata))
    releases = prereleases(json.loads(catalog_path.read_bytes()))
    products = sorted({d for item in releases for d in item['_devices']})
    urls = {f'https://api.appledb.dev/device/{product}.json' for product in products}
    urls |= {f'https://api.ipsw.me/v4/device/{product}?type=ipsw' for product in products}
    urls |= {f"https://api.appledb.dev/ios/iOS;{item['build']}.json" for item in releases}
    print(f'{len(releases)} prerelease builds, {len(products)} product identifiers; fetching {len(urls)} index records.', flush=True)
    fetched, errors = {}, {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_json, url, metadata): url for url in sorted(urls)}
        for future in as_completed(futures):
            url = futures[future]
            try:
                fetched[url] = future.result()
            except Exception as error:
                errors[url] = f'{type(error).__name__}: {error}'
    def replay(url, limit):
        if url not in fetched:
            raise URLError(errors.get(url, 'Unexpected lookup request'))
        data = fetched[url]
        if len(data) > limit:
            raise ValueError('Download exceeded the expected size.')
        return data
    rows, metadata_gaps = [], []
    with patch.object(p, 'get_bytes', side_effect=replay):
        for release in releases:
            for product in release['_devices']:
                device_url = f'https://api.appledb.dev/device/{product}.json'
                device = json.loads(fetched[device_url]) if device_url in fetched else {}
                boards = device.get('board', [])
                boards = [boards] if isinstance(boards, str) else boards
                if not boards:
                    metadata_gaps.append({'product': product, 'build': release['build'], 'error': errors.get(device_url, 'No board metadata')})
                    continue
                for board in boards:
                    row = {'product': product, 'name': device.get('name'), 'hardware': board,
                           'version': release['_version'], 'build': release['build'],
                           'label': release['version'], 'released': release.get('released'),
                           'carrier_payload_test': 'not performed', 'physical_device_test': 'not performed'}
                    try:
                        row['url'] = p.firmware_url(SimpleNamespace(**row))
                        row['lookup'] = 'pass'
                    except Exception as error:
                        row['lookup'] = 'fail'
                        row['error'] = f'{type(error).__name__}: {error}'
                    rows.append(row)
    summary = {'schema': 'lightspeedswap.beta-matrix.v1', 'started_utc': datetime.now(timezone.utc).isoformat(),
               'scope': 'All catalogued iOS 27+ iPhone beta/RC device builds; excludes SDKs and simulators.',
               'code_under_test': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                   for name in ('carrier_payloads.py', 'carrier_remote.py')},
               'snapshot_reused': args.reuse_snapshot,
               'catalog_sha256': hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
               'release_count': len(releases), 'product_count': len(products),
               'device_build_pairs': sum(len(r['_devices']) for r in releases),
               'index_errors': errors, 'metadata_gaps': metadata_gaps, 'rows': rows, 'manifests': [],
               'limitations': ['No physical phones accessed.', 'Carrier filesystem extraction and signatures are not tested by this matrix.',
                               'Discovery replays saved public index bytes; --reuse-snapshot may reuse an earlier snapshot.',
                               'Apple sidecar manifests are labelled separately from manifests extracted via IPSW ranges.']}
    atomic_json(out / 'matrix.json', summary)
    groups = {}
    for row in rows:
        if row['lookup'] == 'pass':
            groups.setdefault(row['url'], []).append(row)
    print(f'Lookup complete: {len(rows)} board/build rows, {len(groups)} unique Apple IPSWs, '
          f'{sum(r["lookup"] == "fail" for r in rows)} lookup failures.', flush=True)
    def check(url, archive=False):
        info, manifest = read_manifest(url, manifests, archive)
        checks = []
        for row in groups[url]:
            entry = {'product': row['product'], 'hardware': row['hardware'], 'build': row['build']}
            try:
                entry['image'] = p.validate_manifest(manifest, SimpleNamespace(**row))
                entry['status'] = 'pass'
            except Exception as error:
                entry['status'] = 'fail'
                entry['error'] = f'{type(error).__name__}: {error}'
            checks.append(entry)
        info['checks'] = checks
        return info
    def run(urls, archive=False):
        finished = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(check, url, archive): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    info = future.result()
                except Exception as error:
                    info = {'url': url, 'source': 'IPSW archive' if archive else 'Apple manifest',
                            'error': f'{type(error).__name__}: {error}', 'checks': []}
                if archive:
                    previous = next((m for m in summary['manifests'] if m['url'] == url and 'sha256' in m), None)
                    if previous and 'sha256' in info:
                        info['sidecar_matches_archive'] = previous['sha256'] == info['sha256']
                    summary.setdefault('archive_checks', []).append(info)
                else:
                    summary['manifests'].append(info)
                finished += 1
                atomic_json(out / 'matrix.json', summary)
                if finished % 20 == 0 or 'error' in info or any(c['status'] != 'pass' for c in info['checks']) or finished == len(urls):
                    print(f'{"Archive" if archive else "Manifest"} {finished}/{len(urls)}; '
                          f'{groups[url][0]["build"]} {groups[url][0]["product"]}: '
                          f'{info.get("error", ", ".join(c["status"] for c in info["checks"]))}', flush=True)
    run(sorted(groups))
    if args.archive_checks != 'none':
        selected = set(groups) if args.archive_checks == 'all' else set()
        seen_builds, seen_products = set(), set()
        for url in sorted(groups, key=lambda u: (groups[u][0]['released'], groups[u][0]['product']), reverse=True):
            build = groups[url][0]['build']
            models = {r['product'] for r in groups[url]}
            if args.archive_checks == 'representative' and (build not in seen_builds or not models <= seen_products):
                selected.add(url)
                seen_builds.add(build)
                seen_products |= models
        print(f'Checking {len(selected)} actual IPSW members via the production range reader.', flush=True)
        run(sorted(selected), archive=True)
    summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
    summary['counts'] = {'lookup_pass': sum(r['lookup'] == 'pass' for r in rows),
                         'lookup_fail': sum(r['lookup'] == 'fail' for r in rows),
                         'manifest_files': len(summary['manifests']),
                         'manifest_download_fail': sum('error' in m for m in summary['manifests']),
                         'manifest_validation_pass': sum(c['status'] == 'pass' for m in summary['manifests'] for c in m['checks']),
                         'manifest_validation_fail': sum(c['status'] == 'fail' for m in summary['manifests'] for c in m['checks']),
                         'archive_files': len(summary.get('archive_checks', [])),
                         'archive_download_fail': sum('error' in m for m in summary.get('archive_checks', [])),
                         'archive_validation_fail': sum(c['status'] == 'fail' for m in summary.get('archive_checks', []) for c in m['checks']),
                         'archive_sidecar_mismatch': sum(m.get('sidecar_matches_archive') is False for m in summary.get('archive_checks', []))}
    atomic_json(out / 'matrix.json', summary)
    print(json.dumps(summary['counts']), flush=True)
    # A complete report is useful even with unavailable IPSWs, but never signal
    # that the entire matrix passed when any lookup or validation is incomplete.
    return int(not rows or bool(errors or metadata_gaps) or any(
        count for name, count in summary['counts'].items() if name.endswith(('_fail', '_mismatch'))))


if __name__ == '__main__':
    sys.exit(main())
