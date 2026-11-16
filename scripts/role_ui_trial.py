#!/usr/bin/env python3
"""Serve the synthetic UI trial and its volatile loopback API; Python stdlib only."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "examples/role-ui-trial"


class TrialServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        super().__init__(("127.0.0.1", port), Handler)
        self.lock = threading.Lock()
        self.requests = 0
        self.tasks = []
        self.generation = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log draft text or browser headers.

    def respond(self, code, data, content_type="application/json; charset=utf-8"):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/tasks":
            with self.server.lock:
                data = {"requests": self.server.requests, "tasks": list(self.server.tasks)}
            self.respond(200, data)
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8")}
        if path not in files:
            self.respond(404, {"error": "not found"})
            return
        filename, mime = files[path]
        self.respond(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        # A browser on another origin must not mutate the loopback fixture.
        expected_origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Origin") not in (None, expected_origin):
            self.respond(403, {"error": "same-origin trial only"})
            return
        path = urlsplit(self.path).path
        if path == "/api/reset":
            with self.server.lock:
                self.server.requests = 0
                self.server.tasks = []
                self.server.generation += 1
            self.respond(200, {"reset": True})
            return
        if path != "/api/tasks":
            self.respond(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("invalid payload size")
            data = json.loads(self.rfile.read(length))
            title = data.get("title")
            if not isinstance(title, str) or not title.strip() or len(title.encode("utf-16-le")) // 2 > 120:
                raise ValueError("title must contain 1–120 characters")
        except (ValueError, AttributeError, UnicodeError):
            self.respond(400, {"error": "invalid title"})
            return
        with self.server.lock:
            self.server.requests += 1
            request_number = self.server.requests
            generation = self.server.generation
        time.sleep(1.2)  # Deliberate API latency, not a test synchronization sleep.
        if request_number == 1:
            self.respond(503, {"error": "intentional first-request failure"})
            return
        with self.server.lock:
            if generation != self.server.generation:
                self.respond(409, {"error": "trial reset during request"})
                return
            task = {"id": len(self.server.tasks) + 1, "title": title}
            self.server.tasks.append(task)
        self.respond(201, task)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    server = TrialServer(args.port)
    ready = {"url": f"http://127.0.0.1:{server.server_port}", "port": server.server_port}
    if args.ready_file:
        path = args.ready_file.resolve()
        allowed = (ROOT / "local/role-skills").resolve()
        linked = any(parent.is_symlink() for parent in (args.ready_file, *args.ready_file.parents))
        if not path.is_relative_to(allowed) or linked:
            server.server_close()
            parser.error("ready-file must be inside local/role-skills")
        if path.exists():
            server.server_close()
            parser.error("ready-file already exists; choose a unique path")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(ready) + "\n")
    print(json.dumps(ready), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
