"""Read-only radio observations. No carrier installer or device writes.

Apple's log strings are undocumented and can disappear or change. Parse only
explicit fields, keep their time/SIM provenance, and never derive a QoS class
from a carrier name, a flow ID, is5G, signal strength, or a speed test.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import math
import re
import threading
import time

SCHEMA = "lightspeedswap.radio.v1"
TTL = 30
MODE_WINDOW = 5
MAX_MESSAGE = 65536
MAX_ROWS = 256
SLOT = re.compile(r"\bslotID\s*[:=]\s*(?:CTSubscriptionSlot|k)?(One|Two)\b")
CELL_KV = re.compile(r'kCTCellMonitor(\w+)["\s]*[:=]\s*["\s]*([\w.\-]+)')
MODE = re.compile(r"(?:reg\.dataMode\s*=\s*|dataMode\s*[:=]\s*k?)(NRNSA|NoData|LTE|NR)\b")
CONNECTED_ROLES = {"Serving", "Primary", "Secondary"}
QOS_MARKER = re.compile(r"\b(?:CTCellularQoSFlow|CTQoS|qosFlows|[tr]xQos|qci)\b")
CELL_MARKER = re.compile(r"\b(?:kCTCellMonitor\w+|CTCellInfo)\b")
REDACTION = re.compile(r"<(?:private|redacted)>", re.I)


def clean(value, length=100):
    value = str(value or "")
    value = re.sub(r"\d{10,}", "[redacted]", value)
    value = re.sub(r"(?i)\b[0-9a-f]{24,}\b", "[redacted]", value)
    return "".join(c for c in value if c.isprintable())[:length]


def number(value, low, high, integer=False):
    try:
        if isinstance(value, bool):
            return None
        n = float(value)
        if not math.isfinite(n) or not low <= n <= high or (integer and n != int(n)):
            return None
        return int(n) if integer else n
    except (ValueError, TypeError, OverflowError):
        return None


def timestamp(value):
    """An imported timestamp without a timezone must not become a live reading."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        return None
    return dt.timestamp()


def field(text, name):
    m = re.search(r"\b" + re.escape(name) + r"\s*[:=]\s*([^,;<>\s]+)", text)
    return m.group(1).strip('"') if m else None


def objects(text, name):
    """Balance nested Objective-C description objects instead of crossing flows."""
    for m in re.finditer(r"<" + re.escape(name) + r"(?=[\s,>])", text):
        level = 0
        for end in range(m.start(), len(text)):
            if text[end] == "<":
                level += 1
            elif text[end] == ">":
                level -= 1
                if level == 0:
                    yield m.start(), text[m.start():end + 1]
                    break


def unique_slot(text):
    slots = set(SLOT.findall(text))
    return next(iter(slots)) if len(slots) == 1 else "Unknown"


def parse_message(message, when=None):
    if not isinstance(message, str) or len(message) > MAX_MESSAGE:
        return []
    epoch = timestamp(when)
    base = {"timestamp": clean(when, 50) or None, "epoch": epoch,
            "slot": unique_slot(message)}
    out = []
    modes = set(MODE.findall(message))
    if len(modes) == 1:
        out.append(dict(base, kind="mode", mode=next(iter(modes))))

    connections = list(re.finditer(r"\binterfaceName\s*=", message))
    for offset, obj in objects(message, "CTCellularQoSFlow"):
        prior = [m.start() for m in connections if m.start() < offset]
        context = message[prior[-1]:offset] if prior else ""
        # A nested second flow cannot overwrite the first flow's tx/rx QoS.
        values = {}
        for direction in ("tx", "rx"):
            match = re.search(direction + r"Qos\s*=\s*", obj)
            if match:
                value = obj[match.end():].lstrip()
                qos = next(objects(value, "CTQoS"), None)
                # A hidden/null TX must not consume a later RX object.
                values[direction] = number(field(qos[1], "qci"), 1, 255, True) if qos and qos[0] == 0 else None
            else:
                values[direction] = None
        if all(v is None for v in values.values()):
            continue
        row = dict(base, kind="qos", **values,
                   apn=clean(field(context, "apnName")) or "Unknown",
                   interface=clean(field(context, "interfaceName"), 32) or "Unknown",
                   state=clean(field(context, "state"), 32) or "Unknown",
                   flow=number(field(obj, "flowId"), 0, 65535, True),
                   bearer=number(field(obj, "bearerId"), 0, 65535, True))
        out.append(row)

    for block in re.findall(r"\{[^{}]*kCTCellMonitor[^{}]*\}", message, re.S):
        kv = dict(CELL_KV.findall(block))
        if "BandInfo" not in kv:
            continue
        rat = kv.get("CellRadioAccessTechnology", "").replace("kCTCellMonitorRadioAccessTechnology", "").upper()
        rat = rat if rat in {"LTE", "NR"} else "Unknown"
        band_text = kv.get("BandInfo", "")
        match = re.fullmatch(r"([Bn]?)(\d{1,4})", band_text)
        band = number(match.group(2), 1, 1024, True) if match else None
        if band is None:
            continue
        # A letter on BandInfo is usable only when it agrees with the RAT.
        if match.group(1) and ((match.group(1) == "B" and rat != "LTE") or
                               (match.group(1) == "n" and rat != "NR")):
            continue
        raw_role = kv.get("CellType", "").replace("kCTCellMonitorCellType", "")
        role = {"Serving": "Serving", "Primary": "Primary", "Secondary": "Secondary",
                "PCell": "Primary", "SCell": "Secondary", "Neighbor": "Neighbor",
                "Neighbour": "Neighbor"}.get(raw_role, "Unknown")
        # A cell ID is only an in-memory key. Never export the location identifier.
        cell_key = hashlib.sha256(kv.get("CellId", "unknown").encode()).hexdigest()[:12]
        out.append(dict(base, kind="cell", rat=rat, band=band, role=role,
                        cell_key=cell_key,
                        rsrp=number(kv.get("RSRP"), -160, -20),
                        rsrq=number(kv.get("RSRQ"), -50, -0.01),
                        sinr=number(kv.get("SINR", kv.get("SNR")), -50, 100),
                        arfcn=number(kv.get("NRARFCN", kv.get("ARFCN")), 0, 4000000, True),
                        bandwidth=clean(kv.get("Bandwidth"), 32) or None))
    return out


def normalize_record(raw):
    """Validate our exported report format on reimport; never accept freshness."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    row = {"kind": kind, "timestamp": clean(raw.get("timestamp"), 50) or None,
           "epoch": timestamp(raw.get("timestamp")),
           "slot": raw.get("slot") if raw.get("slot") in {"One", "Two"} else "Unknown"}
    if kind == "mode" and raw.get("mode") in {"LTE", "NR", "NRNSA", "NoData"}:
        return dict(row, mode=raw["mode"])
    if kind == "qos":
        row.update({k: number(raw.get(k), 1, 255, True) for k in ("tx", "rx")})
        if row["tx"] is None and row["rx"] is None:
            return None
        row.update({k: clean(raw.get(k), 100) or "Unknown" for k in ("apn", "interface", "state")})
        row.update({k: number(raw.get(k), 0, 65535, True) for k in ("flow", "bearer")})
        return row
    if kind == "cell":
        band = number(raw.get("band"), 1, 1024, True)
        if band is None:
            return None
        row.update(band=band, rat=raw.get("rat") if raw.get("rat") in {"LTE", "NR"} else "Unknown",
                   role=raw.get("role") if raw.get("role") in CONNECTED_ROLES | {"Neighbor"} else "Unknown",
                   rsrp=number(raw.get("rsrp"), -160, -20), rsrq=number(raw.get("rsrq"), -50, -0.01),
                   sinr=number(raw.get("sinr"), -50, 100), arfcn=number(raw.get("arfcn"), 0, 4000000, True),
                   bandwidth=clean(raw.get("bandwidth"), 32) or None)
        return row
    return None


class RadioModel:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self, source="none"):
        with self.lock:
            self.source = source
            self.status = "idle"
            self.detail = "Connect an unlocked, trusted iPhone or open a diagnostic capture."
            self.device = {}
            self.rows = {}
            self.modes = deque(maxlen=512)
            self.records_seen = 0
            self.observations_seen = 0
            self.dropped = 0
            self.diagnostics = dict.fromkeys(("qos_candidate_messages", "cell_candidate_messages",
                "unparsed_qos_candidate_messages", "unparsed_cell_candidate_messages",
                "redacted_candidate_messages", "oversized_messages", "mode_events",
                "unattributed_mode_events", "qos_events", "cell_events"), 0)

    def state(self, status, detail):
        with self.lock:
            self.status, self.detail = status, detail

    def feed(self, message, when):
        observations = parse_message(message, when)
        with self.lock:
            self.records_seen += 1
            # Retain only counts over fixed markers, never raw log excerpts.
            if isinstance(message, str):
                if len(message) > MAX_MESSAGE:
                    self.diagnostics["oversized_messages"] += 1
                else:
                    kinds = {row["kind"] for row in observations}
                    candidates = []
                    for kind, marker in (("qos", QOS_MARKER), ("cell", CELL_MARKER)):
                        if marker.search(message):
                            candidates.append(kind)
                            self.diagnostics[kind + "_candidate_messages"] += 1
                            if kind not in kinds:
                                self.diagnostics["unparsed_" + kind + "_candidate_messages"] += 1
                    if candidates and REDACTION.search(message):
                        self.diagnostics["redacted_candidate_messages"] += 1
            for row in observations:
                self.add(row)

    def add(self, row):
        with self.lock:
            self.observations_seen += 1
            self.diagnostics[row["kind"] + "_events"] += 1
            if row["kind"] == "mode":
                if row["slot"] == "Unknown":
                    self.diagnostics["unattributed_mode_events"] += 1
                self.modes.append(row)
                return
            fields = ("slot", "apn", "interface", "bearer", "flow") if row["kind"] == "qos" else ("slot", "rat", "band", "arfcn", "role", "cell_key")
            key = (row["kind"], *(row.get(k) for k in fields))
            old = self.rows.get(key)
            if old and old["epoch"] is not None and (row["epoch"] is None or row["epoch"] < old["epoch"]):
                return
            self.rows.pop(key, None)
            self.rows[key] = row
            if len(self.rows) > MAX_ROWS:
                del self.rows[next(iter(self.rows))]
                self.dropped += 1

    def qos_kind(self, row):
        # Unattributed modes must never classify another SIM's QoS.
        if row["slot"] == "Unknown" or row["epoch"] is None:
            return "QCI / 5QI", None
        candidates = [m for m in self.modes if m["slot"] == row["slot"] and m["epoch"] is not None
                      and 0 <= row["epoch"] - m["epoch"] <= MODE_WINDOW]
        if not candidates:
            return "QCI / 5QI", None
        newest = max(m["epoch"] for m in candidates)
        modes = {m["mode"] for m in candidates if m["epoch"] == newest}
        if len(modes) != 1:
            return "QCI / 5QI", None
        mode = next(iter(modes))
        return {"LTE": "QCI", "NRNSA": "QCI", "NR": "5QI"}.get(mode, "QCI / 5QI"), mode

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            rows = []
            for record in self.rows.values():
                row = dict(record)
                row.pop("cell_key", None)
                age = now - row["epoch"] if row["epoch"] is not None else None
                row["fresh"] = bool(self.source == "live" and self.status == "streaming" and
                                    age is not None and -2 <= age <= TTL)
                row["age_seconds"] = max(0, round(age, 1)) if age is not None and age >= -2 else None
                row["connected"] = bool(row["kind"] == "cell" and row["fresh"] and
                                        row["role"] in CONNECTED_ROLES and row["rat"] in {"LTE", "NR"})
                if row["kind"] == "qos":
                    row["qos_kind"], row["network_mode"] = self.qos_kind(row)
                    row["classification"] = "inferred from same-slot dataMode within 5 seconds" if row["network_mode"] else "unresolved"
                rows.append(row)
            rows.sort(key=lambda r: r["epoch"] if r["epoch"] is not None else -1, reverse=True)
            detail, measurement_state = self.detail, "historical"
            if self.source == "live" and self.status == "streaming":
                measurement_state = "recent" if any(r["fresh"] for r in rows) else "stale" if rows else "none"
                if not rows and self.records_seen:
                    detail = "USB logs are arriving, but no QoS classes or cell bands have been decoded. Mode events alone do not supply either measurement."
            return {"schema": SCHEMA, "source": self.source, "status": self.status, "detail": detail,
                    "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "freshness_seconds": TTL, "device": dict(self.device), "observations": rows,
                    "modes": list(self.modes), "records_seen": self.records_seen,
                    "observations_seen": self.observations_seen, "dropped": self.dropped,
                    "measurements_seen": self.diagnostics["qos_events"] + self.diagnostics["cell_events"],
                    "measurement_state": measurement_state, "diagnostics": dict(self.diagnostics),
                    "coverage": "USB log reception does not establish QoS or band access. Missing, redacted, or unrecognized fields stay unknown; carrier aggregation may be incomplete."}


def demo_model():
    """Explicit synthetic illustration, never an automatic fallback for missing data."""
    model = RadioModel()
    model.reset("demo")
    when = datetime.now(timezone.utc).isoformat()
    model.feed("slotID=CTSubscriptionSlotOne, reg.dataMode=NR (17)", when)
    model.feed("slotID=CTSubscriptionSlotOne, interfaceName=pdp_ip0, apnName=example.data, state=Connected, "
               "<CTCellularQoSFlow, flowId=7, bearerId=5, is5G=true, txQos=<CTQoS, qci=9>, rxQos=<CTQoS, qci=9>>", when)
    for band, power in [(41, -91), (71, -103)]:
        model.feed("slotID=CTSubscriptionSlotOne { kCTCellMonitorCellRadioAccessTechnology = kCTCellMonitorRadioAccessTechnologyNR; "
                   f"kCTCellMonitorBandInfo = {band}; kCTCellMonitorRSRP = {power}; kCTCellMonitorRSRQ = -12; "
                   f"kCTCellMonitorSINR = 18; kCTCellMonitorCellId = {band}; "
                   "kCTCellMonitorCellType = kCTCellMonitorCellTypeServing; }", when)
    model.state("demo", "Synthetic example. These are not measurements from your iPhone.")
    return model
