"""Negative identity controls over every captured real Apple beta/RC manifest."""
import argparse
import copy
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace

import carrier_payloads as p

def rejects(name, manifest, state, counts, failures):
    try:
        p.validate_manifest(manifest, state)
    except ValueError:
        counts[name] = counts.get(name, 0) + 1
    else:
        failures.append({'case': name, 'product': state.product,
                         'hardware': state.hardware, 'build': state.build})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Directory containing the completed matrix.json')
    args = parser.parse_args()
    root = args.output.absolute()
    matrix = json.loads((root / 'matrix.json').read_text())
    rows = {(r['product'], r['hardware'], r['build']): r for r in matrix['rows'] if r['lookup'] == 'pass'}
    variants = {'Customer Erase Install (IPSW)', 'Customer Upgrade Install (IPSW)',
                'Developer Erase Install (IPSW)', 'Developer Upgrade Install (IPSW)'}
    counts, failures = {}, []
    for item in matrix['manifests']:
        original = plistlib.loads(Path(item['file']).read_bytes())
        for check in item['checks']:
            row = rows[(check['product'], check['hardware'], check['build'])]
            state = SimpleNamespace(**row)
            for field, value in [('product', 'iPhone99,99'), ('hardware', 'Z99AP'),
                                 ('version', '27.999'), ('build', '24Z9999')]:
                other = copy.copy(state)
                setattr(other, field, value)
                rejects('wrong_' + field, original, other, counts, failures)
            candidate = next(c for c in original['BuildIdentities']
                             if c['Info']['DeviceClass'].lower() == state.hardware.lower()
                             and c['Info'].get('Variant') in variants)
            ambiguous = dict(original)
            extra = copy.deepcopy(candidate)
            extra['Manifest']['OS']['Info']['Path'] = 'unexpected_alternate.dmg'
            ambiguous['BuildIdentities'] = original['BuildIdentities'] + [extra]
            rejects('ambiguous_filesystem', ambiguous, state, counts, failures)
            unsupported = dict(original)
            unsupported['BuildIdentities'] = []
            for identity in original['BuildIdentities']:
                changed = dict(identity)
                changed['Info'] = dict(identity['Info'])
                if changed['Info']['DeviceClass'].lower() == state.hardware.lower():
                    changed['Info']['Variant'] = 'Unknown Future Install'
                unsupported['BuildIdentities'].append(changed)
            rejects('unknown_variant', unsupported, state, counts, failures)

    report = {'code_under_test': matrix['code_under_test'], 'controls': counts,
              'total_expected_rejections': sum(counts.values()), 'unexpected_acceptances': failures,
              'phone_access': False, 'input': 'captured actual Apple beta/RC manifests'}
    (root / 'negative_manifest_checks.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if failures or not counts:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
