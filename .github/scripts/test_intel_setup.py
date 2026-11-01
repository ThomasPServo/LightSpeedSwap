"""Exercise the shipped zipapp on a real Intel Mac without compiler access.

Seed the incomplete environment left by 0.7.0, retry setup, then repeat setup.
No device command is run. Existing restoration receipts and cache must survive.
"""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from setup_env import MARKER


def main():
    assert sys.platform == 'darwin' and platform.machine() == 'x86_64'
    with tempfile.TemporaryDirectory(prefix='lightspeedswap-intel-') as temporary:
        state = Path(temporary) / 'state with spaces'
        runtime = state / '.carrier-runtime'
        runtime.mkdir(parents=True)
        (runtime / 'owner.txt').write_text(MARKER)
        subprocess.run([sys.executable, '-I', '-m', 'venv', str(runtime / 'env')], check=True)
        private_python = runtime / 'env' / 'bin' / 'python'
        receipts = state / '.carrier-state' / 'receipts.json'
        receipts.parent.mkdir()
        baseline = json.dumps({'schema': 1, 'secret': '0' * 64, 'receipts': {
            '1' * 64: {'baseline': 'mvno', 'device': '2' * 64, 'phase': 'verified'}}}).encode()
        receipts.write_bytes(baseline)
        cache = state / '.carrier-cache' / 'verified-payloads.zip'
        cache.parent.mkdir()
        cache.write_bytes((ROOT / 'bundled.carrierpack').read_bytes())
        original_config = (runtime / 'env' / 'pyvenv.cfg').read_bytes()
        # Missing compilers/OpenSSL must not trigger a build or require Homebrew.
        env = dict(os.environ, CARRIER_SELECTOR_HOME=str(state),
                   CC='/no-compiler/clang', CXX='/no-compiler/clang++',
                   RUSTC='/no-compiler/rustc', CARGO='/no-compiler/cargo',
                   OPENSSL_DIR='/no-openssl')
        for attempt in range(2):
            subprocess.run([sys.executable, str(ROOT / 'dist/LightSpeedSwap.pyz'), 'setup'],
                           env=env, cwd=ROOT, check=True, timeout=600)
            assert receipts.read_bytes() == baseline
            assert cache.read_bytes() == (ROOT / 'bundled.carrierpack').read_bytes()
            assert (runtime / 'env' / 'pyvenv.cfg').read_bytes() == original_config
            assert not list(runtime.glob('env.previous-*'))
            print('Incomplete-runtime repair' if attempt == 0 else 'Repeated setup', 'passed', flush=True)
        probe = (
            'import json,platform,sys; import cryptography; '
            'from cryptography.hazmat.bindings import _rust; '
            'from cryptography.hazmat.backends.openssl.backend import backend; '
            'print(json.dumps(dict(python=platform.python_version(), machine=platform.machine(), '
            'cryptography=cryptography.__version__, openssl=backend.openssl_version_text(), native=_rust.__file__)))'
        )
        report = json.loads(subprocess.check_output([str(private_python), '-I', '-c', probe], env=env, text=True))
        assert report['machine'] == 'x86_64' and report['cryptography'] == '50.0.2'
        linked = subprocess.check_output(['/usr/bin/otool', '-L', report['native']], text=True)
        # otool -L includes LC_ID_DYLIB (the extension's own install name),
        # which is not an external dependency. Obtain that exact name via -D.
        identity = subprocess.check_output(['/usr/bin/otool', '-D', report['native']], text=True)
        own_names = {line.strip() for line in identity.splitlines()[1:]}
        for line in linked.splitlines()[1:]:
            library = line.strip().split(' (', 1)[0]
            assert library in own_names or library.startswith(('/usr/lib/', '/System/Library/')), linked
        print(json.dumps(report, indent=2))
        print(linked)
        print('Intel CLI setup passed without compiler or external OpenSSL linkage; no phone accessed.')


if __name__ == '__main__':
    main()
