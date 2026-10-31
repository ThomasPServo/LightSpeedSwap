#!/bin/bash
set -eu
script_dir="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd -- "$script_dir"
bootstrap=""
for candidate in python3.12 python3.11 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        bootstrap="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$bootstrap" ]; then
    echo "Python was not found. Install Python 3.12 from https://www.python.org/downloads/macos/ and retry." >&2
    exit 2
fi
exec env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 "$bootstrap" -I "$script_dir/setup_env.py" "$@"
