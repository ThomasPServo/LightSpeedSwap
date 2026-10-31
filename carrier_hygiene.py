"""Small local restoration receipts, not device backups or copies of settings.

Only keyed fingerprints of device/SIM identifiers are retained. Receipts do
not provide a device-side daemon or an API for uninstalling carrier bundles.
"""
import hashlib
import hmac
import json
import os

from carrier_paths import data_root
import carrier_store as store


def path(root):
    return data_root(root) / '.carrier-state' / 'receipts.json'


def read(root):
    destination = path(root)
    if not destination.exists():
        return {'schema': 1, 'secret': os.urandom(32).hex(), 'receipts': {}}
    store.ensure(not destination.is_symlink() and destination.stat().st_size <= 128 * 1024,
                 'Invalid restoration receipt file.')
    data = json.loads(destination.read_text())
    store.ensure(isinstance(data, dict) and data.get('schema') == 1
                 and isinstance(data.get('secret'), str) and store.HEX.fullmatch(data['secret'])
                 and isinstance(data.get('receipts'), dict) and len(data['receipts']) <= 128,
                 'Unsupported restoration receipt format. No phone write attempted.')
    for key, entry in data['receipts'].items():
        store.ensure(store.HEX.fullmatch(key) and isinstance(entry, dict)
                     and entry.get('baseline') in {'mvno', 'wholesale'}
                     and isinstance(entry.get('device'), str) and store.HEX.fullmatch(entry['device']),
                     'Invalid restoration receipt.')
    return data


def fingerprint(data, *values):
    return hmac.new(bytes.fromhex(data['secret']), '\0'.join(values).encode(), hashlib.sha256).hexdigest()


def key(data, udid, state):
    store.ensure(bool(udid) and bool(state.imsi) and bool(state.iccid),
                 'Device and SIM identity are required for a restoration receipt.')
    return fingerprint(data, 'sim', udid, state.imsi, state.iccid)


def begin(root, udid, state, requested, baseline=None):
    from carrier_switch import TARGETS
    destination = path(root)
    with store.lock(destination):
        data = read(root)
        ident = key(data, udid, state)
        previous = data['receipts'].get(ident)
        if baseline is not None and previous is not None:
            store.ensure(previous['baseline'] == baseline, 'The supplied original bundle conflicts with the saved receipt.')
        baseline = baseline or (previous['baseline'] if previous else
                                next((k for k, v in TARGETS.items() if v == state.current), None))
        store.ensure(baseline in TARGETS, 'Cannot identify the original bundle; no phone write attempted.')
        store.ensure(previous is not None or len(data['receipts']) < 128,
                     'Restoration receipt limit reached. Preserve the existing receipts before starting another SIM.')
        entry = dict(previous or {}, baseline=baseline, device=fingerprint(data, 'device', udid),
                     requested=requested, phase='prepared', last_version=state.version, last_build=state.build)
        data['receipts'][ident] = entry
        store.atomic_write(destination, json.dumps(data, sort_keys=True).encode())
        return ident, entry


def finish(root, ident, phase):
    destination = path(root)
    with store.lock(destination):
        data = read(root)
        store.ensure(ident in data['receipts'], 'Restoration receipt disappeared; operation status is unknown.')
        data['receipts'][ident]['phase'] = phase
        store.atomic_write(destination, json.dumps(data, sort_keys=True).encode())


def current(root, udid, state):
    if not path(root).exists() or not state.imsi or not state.iccid:
        return None
    data = read(root)
    return data['receipts'].get(key(data, udid, state))


def report(root, udid, state):
    receipt = current(root, udid, state)
    return {'selector_scope': 'full IMSI, GID1 and ICCID',
            'baseline': receipt['baseline'] if receipt else None,
            'phase': receipt.get('phase') if receipt else 'not_recorded_for_this_sim',
            'software_changed_since_last_operation': bool(receipt and
                (receipt.get('last_version'), receipt.get('last_build')) != (state.version, state.build)),
            'legacy_selectors_inspected': False,
            'automatic_device_file_removal': False}
