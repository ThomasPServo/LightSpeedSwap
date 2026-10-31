#!/bin/bash
set -eu
script_dir="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd -- "$script_dir"
exec env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 python3 -s "$script_dir/launcher.py" "$@"
