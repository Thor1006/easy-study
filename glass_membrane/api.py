"""Runtime host: JSON API + Server-Sent Events for Silicate and the gm CLI.

Standard library only. Listens on 127.0.0.1; the Host header is checked to
block DNS rebinding; every POST needs the per-session token. Control requests
call the same runtime methods (and therefore pass the same scheduler,
permission, and state validation) as any other client (Arch §15).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import queue
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .registry import REPO_ROOT

SILICATE_DIR = REPO_ROOT / "silicate"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
_RUN_ACTION = re.compile(r"^/api/runs/([A-Za-z0-9_-]+)/(steer|cap|cancel)$")


class Host:
    def __init__(self, runtime, *, port: int = 0, static_dir: Path = SILICATE_DIR,
                 state_file: str | Path | None = None) -> None:
        self.rt = runtime
        self.port = port
        self.static_dir = Path(static_dir)
        self.state_file = Path(state_file) if state_file else None
        self.token = secrets.token_urlsafe(24)
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, name="gm-loop", daemon=True)
        self._serve_thread: threading.Thread | None = None
        self._clients: set[queue.Queue] = set()
        self._clients_lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ lifecycle
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> Host:
        self._loop_thread.start()
        self.call(self.rt.start())
        self.sync(self.rt.subscribe, self._fanout)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _make_handler(self))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({
                "port": self.port, "token": self.token, "pid": os.getpid(), "demo": self.rt.demo,
                "url": self.url, "started_at": time.time()}), encoding="utf-8")
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        try:
            self.server.serve_forever(poll_interval=0.25)
        finally:
            self.close()

    def serve_in_thread(self) -> threading.Thread:
        self._serve_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1},
                                              name="gm-http", daemon=True)
        self._serve_thread.start()
        return self._serve_thread

    def close(self) -> None:
        with self._clients_lock:
            for q in self._clients:
                q.put(None)
        if self.server:
            if self._serve_thread:
                self.server.shutdown()
            self.server.server_close()
        try:
            self.call(self.rt.stop(), timeout=15)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.state_file and self.state_file.exists():
            try:
                if json.loads(self.state_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    self.state_file.unlink()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ bridging threads -> loop
    def call(self, coro, timeout: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def sync(self, fn, *args, timeout: float = 30):
        future: concurrent.futures.Future = concurrent.futures.Future()

        def runner():
            try:
                future.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
                future.set_exception(exc)

        self.loop.call_soon_threadsafe(runner)
        return future.result(timeout)

    def _fanout(self, event: dict) -> None:
        with self._clients_lock:
            for q in list(self._clients):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def add_client(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self._clients_lock:
            self._clients.add(q)
        return q

    def remove_client(self, q: queue.Queue) -> None:
        with self._clients_lock:
            self._clients.discard(q)


def _make_handler(host: Host):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GlassMembrane"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

        # -- helpers ------------------------------------------------------------
        def _allowed_hosts(self) -> tuple[str, str]:
            return f"127.0.0.1:{host.port}", f"localhost:{host.port}"

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").lower() in self._allowed_hosts()

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        # -- GET ------------------------------------------------------------------
        def do_GET(self) -> None:
            if not self._host_ok():
                return self._error(403, "unexpected Host header")
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            try:
                if path in ("/", "/index.html"):
                    return self._index()
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/state":
                    return self._json(200, host.sync(host.rt.snapshot))
                if path.startswith("/api/runs/") and path.count("/") == 3:
                    return self._json(200, host.sync(host.rt.run_detail, path.rsplit("/", 1)[1]))
                if path == "/api/result":
                    return self._json(200, host.sync(host.rt.result_content, query.get("ref", [""])[0]))
                if path == "/api/events":
                    return self._sse(int(query.get("since", ["0"])[0] or 0))
                return self._error(404, "not found")
            except KeyError as exc:
                return self._error(404, f"not found: {exc}")
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception as exc:  # noqa: BLE001 - report, keep serving
                return self._error(500, f"{type(exc).__name__}: {exc}")

        def _index(self) -> None:
            html = (host.static_dir / "index.html").read_text(encoding="utf-8").replace("{{GM_TOKEN}}", host.token)
            self._send(200, html.encode("utf-8"), STATIC_TYPES[".html"], {"Content-Security-Policy": CSP})

        def _static(self, name: str) -> None:
            target = host.static_dir / name
            if "/" in name or "\\" in name or ".." in name or target.suffix not in STATIC_TYPES or not target.is_file():
                return self._error(404, "not found")
            self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])

        def _sse(self, since: int) -> None:
            q = host.add_client()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                last = since
                for event in host.sync(lambda: [e for e in host.rt.events if e["seq"] > since]):
                    self._sse_write(event)
                    last = event["seq"]
                while True:
                    try:
                        event = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    if event is None:
                        break
                    if event["seq"] > last:
                        self._sse_write(event)
                        last = event["seq"]
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                host.remove_client(q)
                self.close_connection = True

        def _sse_write(self, event: dict) -> None:
            data = json.dumps(event, ensure_ascii=False, default=str)
            self.wfile.write(f"id: {event['seq']}\nevent: gm\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        # -- POST -----------------------------------------------------------------
        def do_POST(self) -> None:
            if not self._host_ok():
                return self._error(403, "unexpected Host header")
            origin = self.headers.get("Origin")
            if origin and origin.lower() not in tuple(f"http://{h}" for h in self._allowed_hosts()):
                return self._error(403, "cross-origin request refused")
            if not secrets.compare_digest(self.headers.get("X-GM-Token", ""), host.token):
                return self._error(403, "missing or invalid session token")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._error(413, "request too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._error(400, "body must be a JSON object")
            path = urlparse(self.path).path
            rt = host.rt
            try:
                if path == "/api/runs":
                    max_agents = body.get("max_agents")
                    run = host.call(rt.submit(str(body.get("text", "")),
                                              max_agents=int(max_agents) if max_agents else None,
                                              source=str(body.get("source", "ui"))[:20],
                                              force_route=bool(body.get("force_route")),
                                              filter_bypass=bool(body.get("filter_bypass"))))
                    return self._json(201, {"run": host.sync(run.to_dict)})
                match = _RUN_ACTION.match(path)
                if not match:
                    return self._error(404, "not found")
                run_id, action = match.groups()
                if action == "steer":
                    result = host.call(rt.steer(run_id, str(body.get("text", "")),
                                                source=str(body.get("source", "ui"))[:20]), timeout=900)
                elif action == "cap":
                    result = host.call(rt.set_cap(run_id, int(body.get("n", 0))))
                else:
                    result = host.call(rt.cancel(run_id))
                return self._json(200, result)
            except KeyError as exc:
                return self._error(404, f"unknown run {exc}")
            except (ValueError, TypeError) as exc:
                return self._error(400, str(exc))
            except (TimeoutError, concurrent.futures.TimeoutError):
                return self._error(504, "the runtime did not respond in time")
            except Exception as exc:  # noqa: BLE001
                return self._error(500, f"{type(exc).__name__}: {exc}")

    return Handler
