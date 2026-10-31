"""Regressions using original bytes from actual, previously unfamiliar IPSWs."""
import io
import plistlib
import unittest
import zipfile

import carrier_payloads as p
import carrier_switch as c
from test_carrier_switch import state


class ActualFirmwareMatrix(unittest.TestCase):
    def test_previously_unfamiliar_older_and_separate_device_builds(self):
        cases = [('iPhone13,2', 'D53GAP', '27.0', '24A437', '72.0', 'D52g_D53g_D53p_D54p'),
                 ('iPhone13,3', 'D53PAP', '27.0', '24A437', '72.0', 'D52g_D53g_D53p_D54p'),
                 ('iPhone19,4', 'V68AP', '27.2', '24B91020', '73.0', 'V63_V64s_V68')]
        for product, board, version, build, carrier, group in cases:
            st = state(product=product, hardware=board, version=version, build=build)
            st.line['CFBundleVersion'] = carrier
            for target in c.TARGETS:
                with self.subTest(product=product, target=target):
                    original = c.load_files(target, st)
                    info, signatures = p.validate_files(target, original)
                    self.assertEqual(info['CFBundleVersion'], carrier)
                    self.assertIn(group, signatures)
                    self.assertTrue(all(v['signature_valid'] for v in signatures.values()))
                    package = c.make_package(st, target)
                    with zipfile.ZipFile(io.BytesIO(package)) as z:
                        prefix = 'Payload/' + c.selector_name(st) + '/'
                        files = {n[len(prefix):]: z.read(n) for n in z.namelist() if not n.endswith('/')}
                    self.assertEqual(files, original)


if __name__ == '__main__':
    unittest.main()
