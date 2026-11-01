"""Local, token-protected radio dashboard and optional paired USB collector."""
import asyncio
import contextlib
from datetime import timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit
import webbrowser

from carrier_radio import RadioModel, demo_model
from carrier_radio_import import feed_record, import_capture


class Collector:
    def __init__(self, udid=None):
        self.model = RadioModel()
        self.udid = udid
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.stop_event.clear()
            self.model = RadioModel()
            self.model.reset("live")
            self.model.state("connecting", "Connecting to one trusted iPhone over USB…")
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True

    def stop(self):
        self.stop_event.set()
        self.model.state("paused", "Collection paused. Last observations are historical.")

    def _run(self):
        try:
            asyncio.run(self._collect())
        except Exception as exc:
            self.model.state("unavailable", "USB logging unavailable (" + type(exc).__name__ + "). Unlock/trust the iPhone and check the runtime. No QoS value was inferred.")

    async def _collect(self):
        from carrier_switch import Phone
        from pymobiledevice3.services.os_trace import OsTraceService, OsActivityStreamFlag as F
        phone = Phone(self.udid)
        stream = lookup = None
        tasks = []
        try:
            await phone.connect(autopair=False)
            initial = await phone.snapshot()
            identity = self._metadata(initial)
            lookup = OsTraceService(phone.device)
            response = await asyncio.wait_for(lookup.get_pid_list(), 8)
            pids = [int(pid) for pid, row in response.get("Payload", {}).items()
                    if isinstance(row, dict) and Path(str(row.get("ProcessName", ""))).name == "CommCenter"]
            if len(pids) != 1:
                raise ValueError("CommCenter was not identified uniquely")
            await asyncio.wait_for(lookup.close(), 2)
            lookup = None
            stream = OsTraceService(phone.device)
            # No HISTORICAL: old events must never become current readings.
            flags = F.PROCESS_ONLY | F.PAYLOAD | F.DEBUG | F.INFO | F.NO_SENSITIVE

            async def consume():
                async for entry in stream.syslog(pid=pids[0], stream_flags=flags):
                    if self.stop_event.is_set():
                        return
                    when = entry.timestamp
                    if when.tzinfo is None:
                        when = when.astimezone()  # pymobiledevice3 uses host-local fromtimestamp
                    self.model.feed(entry.message, when.astimezone(timezone.utc).isoformat())

            async def monitor():
                previous = identity
                while not self.stop_event.is_set():
                    for _ in range(20):
                        if self.stop_event.is_set():
                            return
                        await asyncio.sleep(1)
                    state = await asyncio.wait_for(phone.snapshot(), 30)
                    current = self._metadata(state)
                    if current != previous:
                        with self.model.lock:
                            self.model.rows.clear(); self.model.modes.clear()
                        previous = current

            self.model.state("streaming", "Listening to current CommCenter events. Generate cellular traffic; some firmware does not expose these fields.")
            tasks = [asyncio.create_task(consume()), asyncio.create_task(monitor())]
            while not self.stop_event.is_set():
                done, _ = await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                if done:
                    for task in done:
                        task.result()
                    break
            if not self.stop_event.is_set():
                self.model.state("disconnected", "The USB log stream ended. Last observations are historical.")
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            for service in (stream, lookup):
                if service:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(service.close(), 2)
            await phone.close()

    def _metadata(self, state):
        rows = [{k: row.get(k, "") for k in ("CFBundleIdentifier", "CFBundleVersion", "Slot")} for row in state.rows]
        with self.model.lock:
            self.model.device = {"product": state.product, "version": state.version, "build": state.build, "lines": rows}
        # Compare only in memory. Never expose or persist subscriber identifiers.
        return hashlib.sha256(repr((state.iccid, state.imsi, state.rows)).encode()).digest()


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # route selection; sends no packet
        value = sock.getsockname()[0]
        return value if ipaddress.ip_address(value).is_private else None
    except OSError:
        return None
    finally:
        sock.close()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, collector, token=None, share_ip=None):
        self.collector = collector
        self.token = token or secrets.token_urlsafe(32)
        self.share_ip = share_ip
        self.html = Path(__file__).with_name("radio_dashboard.html").read_text(encoding="utf-8")
        super().__init__(address, DashboardHandler)

    def phone_url(self):
        return "http://" + self.share_ip + ":" + str(self.server_port) + "/#token=" + self.token if self.share_ip else None


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Tokens, imported filenames and requests never go into access logs.

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def allowed(self, token=True):
        host = self.headers.get("Host", "")
        hosts = {"127.0.0.1", "localhost", self.server.share_ip}
        if host not in {h + ":" + str(self.server.server_port) for h in hosts if h}:
            self.reply(403, {"error":"Unrecognized local host"}); return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            self.reply(403, {"error":"Different origin"}); return False
        if token and not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self.reply(401, {"error":"Open the full access link printed in the terminal."}); return False
        return True

    def reply(self, code, data, kind="application/json", extra=None):
        payload = json.dumps(data, allow_nan=False).encode() if kind == "application/json" else data.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(token=path != "/"):
            return
        if path == "/":
            nonce = secrets.token_urlsafe(20)
            self.reply(200, self.server.html.replace("__NONCE__", nonce), "text/html", {
                "Content-Security-Policy": "default-src 'none'; connect-src 'self'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"})
        elif path == "/api/state":
            self.reply(200, self.server.collector.model.snapshot())
        elif path == "/api/share":
            self.reply(200, {"url": self.server.phone_url()})
        else:
            self.reply(404, {"error":"Not found"})

    def do_POST(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        if path == "/api/start":
            started = self.server.collector.start()
            self.reply(200 if started else 409, {"started":started})
        elif path == "/api/stop":
            self.server.collector.stop()
            self.reply(200, {"stopped":True})
        elif path == "/api/import":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Import a JSON report under 8 MiB.")
                record = json.loads(self.rfile.read(length))
                collector = self.server.collector
                if collector.thread and collector.thread.is_alive():
                    raise ValueError("Pause the USB collector and wait for it to close before importing.")
                model = RadioModel(); model.reset("historical")
                for item in record if isinstance(record, list) else [record]:
                    feed_record(model, item)
                model.state("imported", "Saved capture. These are historical observations.")
                collector.model = model
                self.reply(200, {"imported":model.observations_seen})
            except (ValueError, TypeError, KeyError, AttributeError):
                self.reply(400, {"error":"Pause live capture, then select a valid JSON report under 8 MiB. Use --import for JSONL/logarchives."})
        else:
            self.reply(404, {"error":"Not found"})


def run(args):
    if not 0 <= args.port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")
    collector = Collector(args.udid)
    if args.demo:
        collector.model = demo_model()
    elif args.import_path:
        collector.model = import_capture(args.import_path)
    share_ip = local_ip() if args.share else None
    server = DashboardServer(("0.0.0.0" if args.share else "127.0.0.1", args.port), collector, share_ip=share_ip)
    url = "http://127.0.0.1:" + str(server.server_port) + "/#token=" + server.token
    print("Radio dashboard: " + url, flush=True)
    if args.share:
        print("Phone link: " + (server.phone_url() or "No LAN address found; use the Mac's local IPv4 address with this port and token."), flush=True)
        print("Local HTTP sharing is for a trusted Wi-Fi network. The access key changes each launch.", flush=True)
    if not (args.demo or args.import_path or args.offline):
        collector.start()
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        server.server_close()
    return 0
