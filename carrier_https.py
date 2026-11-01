"""Verified HTTPS with Mozilla roots plus the interpreter's configured trust.

Some macOS Python installs have no populated default CA file. Do not depend on
running python.org's external certificate installer, or disable TLS verification.
"""
from functools import lru_cache
import ssl


@lru_cache(maxsize=1)
def https_context():
    try:
        import certifi
    except ImportError as exc:
        raise ValueError('HTTPS certificates are missing. Run the app with setup again.') from exc
    # Keep platform/configured roots (including SSL_CERT_FILE/SSL_CERT_DIR), then
    # add the explicit runtime dependency. Both sources remain trusted.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context
