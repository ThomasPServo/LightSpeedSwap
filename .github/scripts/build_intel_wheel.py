"""Build the pinned Intel wheel on a disposable GitHub-hosted Mac, never a phone."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def run(args, cwd, env, capture=False):
    return subprocess.run([str(v) for v in args], cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE if capture else None).stdout


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin' or platform.machine() != 'x86_64':
        raise SystemExit('This builder runs only on a disposable Intel Mac Actions runner.')
    request = json.loads((ROOT / '.github/intel-wheel-request.json').read_text())
    destination = ROOT / 'vendor' / 'intel-macos'
    if destination.exists():
        raise SystemExit('Refusing to overwrite an existing wheel directory.')
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='lightspeedswap-wheel-') as temporary:
        work = Path(temporary)
        source = {}
        for name in ('openssl', 'cryptography'):
            item = request[name]
            archive = work / (name + '.tar.gz')
            with urllib.request.urlopen(item['url'], timeout=120) as response:
                data = response.read(128 * 1024 * 1024 + 1)
            if len(data) > 128 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise SystemExit(name + ' source checksum mismatch.')
            archive.write_bytes(data)
            with tarfile.open(archive) as tar:
                tar.extractall(work, filter='data')
            source[name] = work / (name + '-' + item['version'])
        prefix = work / 'openssl-install'
        env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=request['macos_deployment_target'])
        run(['./Configure', 'darwin64-x86_64-cc', 'no-shared', 'no-module', 'no-tests',
             '--prefix=' + str(prefix), '--openssldir=' + str(prefix / 'ssl')], source['openssl'], env)
        run(['make', '-j3'], source['openssl'], env)
        run(['make', 'install_sw'], source['openssl'], env)
        env.update(OPENSSL_DIR=str(prefix), OPENSSL_STATIC='1',
                   CARGO_TARGET_DIR=str(work / 'cargo-target'))
        run([sys.executable, '-m', 'pip', 'wheel', '--no-cache-dir', '--no-deps',
             '--wheel-dir', destination, source['cryptography']], work, env)
        wheels = list(destination.glob('*.whl'))
        if len(wheels) != 1 or not wheels[0].name.endswith('-cp311-abi3-macosx_10_15_x86_64.whl'):
            raise SystemExit('Unexpected wheel output: ' + str(wheels))
        wheel = wheels[0]
        run([sys.executable, '-m', 'pip', 'install', 'pymobiledevice3==11.49.1', wheel], ROOT, env)
        probe = ('import json; import cryptography; from cryptography.hazmat.bindings import _rust; '
                 'from cryptography.hazmat.backends.openssl.backend import backend; '
                 'print(json.dumps(dict(cryptography=cryptography.__version__, '
                 'openssl=backend.openssl_version_text(), native=_rust.__file__)))')
        versions = json.loads(run([sys.executable, '-I', '-c', probe], ROOT, env, capture=True))
        if versions['cryptography'] != request['cryptography']['version'] or not versions['openssl'].startswith('OpenSSL ' + request['openssl']['version'] + ' '):
            raise SystemExit('Unexpected built library versions: ' + str(versions))
        linked = run(['/usr/bin/otool', '-L', versions['native']], ROOT, env, capture=True)
        own = set(line.strip() for line in run(['/usr/bin/otool', '-D', versions['native']], ROOT, env, capture=True).splitlines()[1:])
        for line in linked.splitlines()[1:]:
            lib = line.strip().split(' (', 1)[0]
            if lib not in own and not lib.startswith(('/usr/lib/', '/System/Library/')):
                raise SystemExit('External library dependency: ' + lib)
        run([sys.executable, 'carrier_switch.py', 'doctor'], ROOT, env)
        run([sys.executable, '-m', 'unittest', '-q'], ROOT, env)
        (destination / 'OpenSSL-LICENSE.txt').write_bytes((source['openssl'] / 'LICENSE.txt').read_bytes())
        receipt = dict(request, wheel=wheel.name, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                       bytes=wheel.stat().st_size, versions={k:v for k,v in versions.items() if k != 'native'},
                       build_commit=os.environ['GITHUB_SHA'], run_id=os.environ['GITHUB_RUN_ID'],
                       rust=run(['rustc', '--version'], ROOT, env, capture=True).strip(),
                       linkage=linked.splitlines()[1:])
        (destination / 'provenance.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt, indent=2), flush=True)


if __name__ == '__main__':
    main()
