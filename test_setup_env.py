"""Windows runtime selection without downloading or accessing a device."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
