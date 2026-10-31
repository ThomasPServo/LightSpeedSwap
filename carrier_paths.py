"""Separate read-only application files from user-owned runtime/cache files."""
import os
from pathlib import Path


def data_root(application_root):
    override = os.environ.get('CARRIER_SELECTOR_HOME')
    if not override:
        return Path(application_root)
    path = Path(override).expanduser()
    if not path.is_absolute():
        raise ValueError('CARRIER_SELECTOR_HOME must be an absolute directory path.')
    return path
