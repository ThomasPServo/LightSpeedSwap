"""Bounded local import of decoded JSON logs or an Apple .logarchive on macOS."""
import contextlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

from carrier_radio import RadioModel, SCHEMA, normalize_record

MAX_RECORD = 2 * 1024 * 1024
MAX_TEXT = 512 * 1024 * 1024
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024


def json_records(stream):
    """Stream either a JSON array or adjacent JSON/JSONL records with hard bounds."""
    decoder, buffer, total = json.JSONDecoder(), "", 0
    array = None
    ended = False
    while True:
        chunk = stream.read(65536)
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        total += len(chunk)
        if total > MAX_TEXT:
            raise ValueError("Decoded log exceeds the 512 MiB import limit.")
        buffer += chunk
        if array is None and buffer.strip():
            buffer = buffer.lstrip("\ufeff \r\n\t")
            array = buffer.startswith("[")
            if array:
                buffer = buffer[1:]
        while buffer.strip():
            buffer = buffer.lstrip()
            if ended:
                raise ValueError("Unexpected data after the JSON array.")
            if array and buffer.startswith(","):
                buffer = buffer[1:].lstrip()
            if array and buffer.startswith("]"):
                ended = True
                buffer = buffer[1:].lstrip()
                continue
            try:
                obj, consumed = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            if consumed > MAX_RECORD:
                raise ValueError("One JSON record exceeds the 2 MiB limit.")
            yield obj
            buffer = buffer[consumed:]
        if len(buffer) > MAX_RECORD:
            raise ValueError("Invalid or oversized JSON record.")
        if not chunk:
            if buffer.strip() or (array and not ended):
                raise ValueError("The JSON log is incomplete or malformed.")
            return


def feed_record(model, record):
    if not isinstance(record, dict):
        return
    if record.get("schema") == SCHEMA:
        for raw in record.get("observations", []) + record.get("modes", []):
            row = normalize_record(raw)
            if row:
                model.add(row)
        return
    events = record.get("trace", {}).get("events") if isinstance(record.get("trace"), dict) else None
    if isinstance(events, list):
        for item in events:
            feed_record(model, item)
        return
    message = record.get("eventMessage", record.get("message", ""))
    model.feed(message, record.get("timestamp", record.get("date")))


def extract_logarchive(source, folder):
    """Only copy regular .logarchive files. Never extract device archive paths."""
    started, remaining, count = time.monotonic(), MAX_ARCHIVE, 0
    roots = set()

    def unpack(path, depth=0):
        nonlocal remaining, count
        if depth > 2:
            raise ValueError("Diagnostic archive nesting exceeds the limit.")
        with tarfile.open(path, "r|*") as tar:
            for member in tar:
                count += 1
                if count > 100000 or time.monotonic() - started > 180:
                    raise ValueError("Diagnostic archive exceeds the import limit.")
                p = PurePosixPath(member.name)
                if p.is_absolute() or ".." in p.parts or "\\" in member.name or not (member.isdir() or member.isfile()):
                    raise ValueError("Unsafe diagnostic archive member.")
                if member.isdir():
                    continue
                locations = [i for i, part in enumerate(p.parts) if part.endswith(".logarchive")]
                nested = member.name.endswith((".tar", ".tar.gz", ".tgz"))
                if not locations and not nested:
                    continue
                if member.size > remaining:
                    raise ValueError("Diagnostic archive expands beyond the 2 GiB limit.")
                remaining -= member.size
                if locations:
                    index = locations[0]
                    roots.add(p.parts[:index + 1])
                    if len(roots) != 1:
                        raise ValueError("Choose one logarchive; multiple captures are not merged.")
                    target = folder / "capture.logarchive" / Path(*p.parts[index + 1:])
                else:
                    target = folder / ("nested-" + str(count) + ".tar")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if not locations:
                    unpack(target, depth + 1)
                    target.unlink()
    unpack(source)
    path = folder / "capture.logarchive"
    if not roots or not path.is_dir():
        raise ValueError("No .logarchive was found in this diagnostic capture.")
    return path


def import_capture(path):
    model = RadioModel()
    model.reset("historical")
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="lightspeed-radio-import-") as folder:
        if path.is_file() and path.name.endswith((".tar", ".tar.gz", ".tgz")):
            path = extract_logarchive(path, Path(folder))
        if path.is_dir():
            if path.suffix != ".logarchive" or sys.platform != "darwin":
                raise ValueError("Binary .logarchive decoding requires macOS. Import decoded JSON/JSONL on other systems.")
            predicate = 'eventMessage CONTAINS "CTCellularQoSFlow" OR eventMessage CONTAINS "kCTCellMonitor" OR eventMessage CONTAINS "dataMode"'
            process = subprocess.Popen(["/usr/bin/log", "show", "--archive", str(path.resolve()),
                                        "--style", "json", "--info", "--debug", "--predicate", predicate],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            timer = threading.Timer(180, process.kill)
            timer.daemon = True
            timer.start()
            try:
                for record in json_records(process.stdout):
                    feed_record(model, record)
                if process.wait(timeout=5) != 0:
                    raise ValueError("macOS could not decode the archive, or the 180-second limit expired.")
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.stdout.close()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
        else:
            with path.open("r", encoding="utf-8-sig") as stream:
                for record in json_records(stream):
                    feed_record(model, record)
    model.state("imported", "Saved capture. Observations are historical, not the current connection." if model.rows else
                "No usable QoS or band fields were found. Missing or private logs do not establish a QoS value.")
    return model
