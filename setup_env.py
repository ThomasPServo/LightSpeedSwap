#!/usr/bin/env python3
"""Prepare a private Python environment. This bootstrap works with Python 3.9.

Downloads dependencies and, when needed, an Astral-managed Python 3.12.
Does not contact an iPhone, alter system Python, change PATH, or delete backups.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from carrier_paths import data_root

UV_VERSION = "0.13.6"
MARKER = "carrier-selector-local-runtime-v1\n"
PROBE = (
    "import sys,ssl; "
    "sys.exit(0 if (3,11)<=sys.version_info[:2] "
    "and ssl.OPENSSL_VERSION.startswith('OpenSSL ') "
    "and ssl.OPENSSL_VERSION_INFO[:3]>=(1,1,1) else 1)"
)


def clean_environment():
    # Avoid inheriting an old user-site, external venv, index, or uv configuration.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX")
           and not k.startswith(("UV_", "PIP_"))}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def executable(folder, name="python"):
    return folder / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))


def compatible(candidate, env):
    if not candidate:
        return False
    try:
        return subprocess.run([str(candidate), "-I", "-c", PROBE], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(args, env, root, *, capture=False):
    result = subprocess.run([str(v) for v in args], cwd=root, env=env, check=True,
                            stdout=subprocess.PIPE if capture else None, text=True)
    return result.stdout.strip() if capture else None


def select_python(runtime, root, env, *, managed_only=False):
    private_python = executable(runtime / "env")
    if compatible(private_python, env):
        return private_python
    candidates = [shutil.which(name) for name in ("python3.12", "python3.11", "python3.13", "python3.14")]
    candidates += [sys.executable, shutil.which("python3")]
    if sys.platform == "darwin":
        for prefix in ("/opt/homebrew", "/usr/local"):
            candidates.append(prefix + "/opt/python@3.12/bin/python3.12")
        candidates.append("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12")
    for candidate in ([] if managed_only else candidates):
        if compatible(candidate, env):
            return Path(candidate)

    print("Preparing a private managed Python 3.12...", flush=True)
    bootstrap = runtime / "bootstrap"
    bootstrap_python = executable(bootstrap)
    if not bootstrap_python.exists():
        run([sys.executable, "-I", "-m", "venv", bootstrap], env, root)
    run([bootstrap_python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
         "--index-url", "https://pypi.org/simple", "uv==" + UV_VERSION], env, root)
    uv = executable(bootstrap, "uv")
    managed = dict(env, UV_PYTHON_INSTALL_DIR=str(runtime / "python"), UV_CACHE_DIR=str(runtime / "cache"))
    run([uv, "--no-config", "python", "install", "3.12", "--no-bin", "--no-registry"], managed, root)
    candidate = Path(run([uv, "--no-config", "python", "find", "3.12", "--managed-python", "--no-project"],
                         managed, root, capture=True))
    if not compatible(candidate, env):
        raise RuntimeError("The downloaded Python did not pass the Python/OpenSSL check.")
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-python", action="store_true", help="Download private Python 3.12 instead of selecting a system interpreter")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        print("Setup needs Python 3.9+ to bootstrap. Install Python 3.12 from python.org first.", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent
    runtime = data_root(root) / ".carrier-runtime"
    marker = runtime / "owner.txt"
    if runtime.is_symlink() or (runtime.exists() and not marker.is_file()):
        raise RuntimeError("The private runtime directory already exists without its ownership marker; leave it intact and extract a fresh copy.")
    if marker.exists() and marker.read_text() != MARKER:
        raise RuntimeError("Unrecognized runtime directory; extract a fresh copy instead of overwriting it.")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker.write_text(MARKER)
    env = clean_environment()
    selected = select_python(runtime, root, env, managed_only=args.managed_python)
    destination = runtime / "env"
    private_python = executable(destination)
    if not compatible(private_python, env):
        if destination.exists():
            # Preserve an old/broken environment instead of deleting user files.
            index = 1
            while (runtime / ("env.previous-" + str(index))).exists():
                index += 1
            destination.rename(runtime / ("env.previous-" + str(index)))
        run([selected, "-I", "-m", "venv", destination], env, root)
    print("Installing pinned requirements into the private environment...", flush=True)
    run([private_python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
         "--index-url", "https://pypi.org/simple", "--upgrade", "-r", root / "requirements.txt"], env, root)
    run([private_python, "-I", "-m", "pip", "check"], env, root)
    run([private_python, "-s", root / "carrier_switch.py", "doctor"], env, root)
    if (root / 'test_carrier_switch.py').is_file():
        run([private_python, "-s", "-m", "unittest", "-v"], env, root)
    print("\nSetup and offline checks passed. No phone was accessed.", flush=True)
    print("Next: run the app with status", flush=True)
    print("Private Python: " + str(private_python), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Setup stopped: " + str(exc), file=sys.stderr)
        print("No phone operation was attempted. Fix the setup error and run setup again.", file=sys.stderr)
        raise SystemExit(2)
