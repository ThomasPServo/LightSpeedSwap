"""Runtime and dependency selection without downloads or device access."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from packaging.markers import default_environment
from packaging.requirements import Requirement

import setup_env as setup


class RuntimeArchitecture(unittest.TestCase):
    def test_probe_requires_x64_on_windows_and_accepts_linux_arm(self):
        for system, architecture, expected in [('win32', 'win-amd64', 0),
                                                ('win32', 'win-arm64', 1),
                                                ('win32', 'win32', 1),
                                                ('linux', 'linux-aarch64', 0)]:
            with self.subTest(system=system, architecture=architecture):
                prefix = ('import sys,ssl,sysconfig; '
                          'sys.platform=' + repr(system) + '; '
                          'sysconfig.get_platform=lambda:' + repr(architecture) + '; '
                          'sysconfig.get_config_var=lambda name:0; '
                          "ssl.OPENSSL_VERSION='OpenSSL 3.0'; ssl.OPENSSL_VERSION_INFO=(3,0,0); ")
                result = subprocess.run([sys.executable, '-I', '-c', prefix + setup.PROBE], timeout=20)
                self.assertEqual(result.returncode, expected)

    def test_managed_windows_request_is_explicitly_x64(self):
        calls = []

        def run(args, env, root, *, capture=False):
            calls.append(args)
            return str(root / 'managed-python') if capture else None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(setup.sys, 'platform', 'win32'), \
                    patch.object(setup.shutil, 'which', return_value=None), \
                    patch.object(setup, 'compatible', side_effect=[False, True]), \
                    patch.object(setup, 'run', side_effect=run):
                self.assertEqual(setup.select_python(root / 'runtime', root, {}, managed_only=True),
                                 root / 'managed-python')
        commands = [args for args in calls if 'python' in args and ('install' in args or 'find' in args)]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all('cpython-3.12-windows-x86_64' in args for args in commands))

    def test_current_supported_python_is_preferred_to_another_system_python(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(setup, 'compatible', side_effect=[False, True]) as probe:
            self.assertEqual(setup.select_python(Path(tmp), Path(tmp), {}), Path(sys.executable))
            self.assertEqual(probe.call_args.args[0], sys.executable)

    def test_existing_partial_install_reuses_its_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(setup, 'compatible', return_value=True), \
                patch.object(setup, 'run') as run:
            runtime = Path(tmp)
            self.assertEqual(setup.select_python(runtime, runtime, {}), setup.executable(runtime / 'env'))
            run.assert_not_called()

    def test_probe_rejects_interpreters_incompatible_with_abi3_wheels(self):
        for implementation, free_threaded, expected in [('cpython', 0, 0),
                                                       ('cpython', 1, 1), ('pypy', 0, 1)]:
            with self.subTest(implementation=implementation, free_threaded=free_threaded):
                prefix = ('import sys,ssl,sysconfig; from types import SimpleNamespace; '
                          'sys.platform="darwin"; '
                          'sys.implementation=SimpleNamespace(name=' + repr(implementation) + '); '
                          'sysconfig.get_config_var=lambda name:' + repr(free_threaded) + '; '
                          "ssl.OPENSSL_VERSION='OpenSSL 3.0'; ssl.OPENSSL_VERSION_INFO=(3,0,0); ")
                result = subprocess.run([sys.executable, '-I', '-c', prefix + setup.PROBE], timeout=20)
                self.assertEqual(result.returncode, expected)


class DependencySelection(unittest.TestCase):
    def test_only_intel_macos_uses_the_exact_hashed_vendor_wheel(self):
        lines = (Path(__file__).parent / 'requirements.txt').read_text().splitlines()
        requirements = [Requirement(line) for line in lines if line and not line.startswith(('#', '--'))]
        self.assertIn('--only-binary=cryptography', lines)
        for system, machine in [('darwin', 'x86_64'), ('darwin', 'arm64'),
                                ('win32', 'AMD64'), ('linux', 'x86_64'), ('linux', 'aarch64')]:
            with self.subTest(system=system, machine=machine):
                environment = dict(default_environment(), sys_platform=system, platform_machine=machine)
                selected = [r for r in requirements if r.name == 'cryptography' and
                            (r.marker is None or r.marker.evaluate(environment))]
                self.assertEqual(len(selected), 1)
                if (system, machine) == ('darwin', 'x86_64'):
                    from urllib.parse import urlsplit
                    url = urlsplit(selected[0].url)
                    self.assertEqual((url.scheme, url.netloc), ('https', 'dl.espressif.com'))
                    self.assertTrue(url.path.endswith('cryptography-50.0.2-cp311-abi3-macosx_10_12_x86_64.whl'))
                    self.assertRegex(url.fragment, r'^sha256=[0-9a-f]{64}$')
                else:
                    self.assertIsNone(selected[0].url)
                    self.assertEqual(str(selected[0].specifier), '==50.0.2')


if __name__ == '__main__':
    unittest.main()
