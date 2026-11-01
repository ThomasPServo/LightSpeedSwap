"""Exercise real TLS handshakes without network access or a system CA file."""
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import carrier_https
import carrier_payloads


def certificate(name, key, issuer=None, issuer_key=None, *, ca=False):
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer.subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                   (issuer_key or key).public_key()), critical=False))
    if ca:
        builder = builder.add_extension(x509.KeyUsage(True, False, False, False, False,
                                                      True, True, False, False), critical=True)
    else:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


class HTTPSContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.ca_files, cls.servers = [], []
        for number in range(2):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ca = certificate('Test root ' + str(number), key, ca=True)
            ca_path = cls.root / ('ca' + str(number) + '.pem')
            ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
            cls.ca_files.append(str(ca_path))
            leaf = certificate('localhost', key, ca, key)
            chain = cls.root / ('chain' + str(number) + '.pem')
            chain.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
            private = cls.root / ('key' + str(number) + '.pem')
            private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                  serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(chain, private)
            cls.servers.append(server)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def tearDown(self):
        carrier_https.https_context.cache_clear()

    def handshake(self, client_context, server_context, hostname='localhost'):
        incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
        # Python 3.13+ enables strict X.509 validation by default. Exercise it
        # on older test interpreters too; fixtures must meet the same rules.
        client_context.verify_flags |= ssl.VERIFY_X509_STRICT
        client = client_context.wrap_bio(incoming, outgoing, server_hostname=hostname)
        server = server_context.wrap_bio(server_in, server_out, server_side=True)
        complete = [False, False]
        for _ in range(20):
            for index, peer in enumerate((client, server)):
                if not complete[index]:
                    try:
                        peer.do_handshake()
                        complete[index] = True
                    except ssl.SSLWantReadError:
                        pass
            server_in.write(outgoing.read())
            incoming.write(server_out.read())
            if all(complete):
                return
        self.fail('TLS handshake did not complete')

    def test_missing_python_roots_repaired_without_accepting_untrusted_or_wrong_host(self):
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch('carrier_https.ssl.create_default_context', return_value=empty), \
             patch('certifi.where', return_value=self.ca_files[0]):
            context = carrier_https.https_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.handshake(context, self.servers[0])
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.handshake(context, self.servers[1])
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.handshake(context, self.servers[0], 'wrong.example')

    def test_explicit_python_trust_is_preserved_alongside_certifi(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': self.ca_files[1],
                                     'SSL_CERT_DIR': str(self.root / 'absent')}), \
             patch('certifi.where', return_value=self.ca_files[0]):
            context = carrier_https.https_context()
        for server in self.servers:
            self.handshake(context, server)

    def test_metadata_download_passes_verified_context_and_does_not_retry_insecurely(self):
        with patch('carrier_payloads.urllib.request.urlopen', return_value=io.BytesIO(b'{}')) as fetch:
            self.assertEqual(carrier_payloads.get_bytes('https://api.ipsw.me/fixture', 100), b'{}')
            context = fetch.call_args.kwargs['context']
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
        with patch('carrier_payloads.urllib.request.urlopen',
                   side_effect=ssl.SSLCertVerificationError('untrusted')) as fetch:
            with self.assertRaises(ssl.SSLCertVerificationError):
                carrier_payloads.get_bytes('https://api.ipsw.me/fixture', 100)
            self.assertEqual(fetch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
