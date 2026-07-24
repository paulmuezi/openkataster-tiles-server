#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("ok", "redirect", "wrong-json"), required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--requests-file", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _write(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            with args.requests_file.open("a", encoding="utf-8") as request_log:
                request_log.write(f"{self.path}\n")

            if self.path == "/health":
                if args.scenario == "redirect":
                    body = b"<html><body>not the health endpoint</body></html>"
                    self.send_response(303)
                    self.send_header("Location", "/health-page")
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if args.scenario == "wrong-json":
                    self._write(
                        200,
                        "application/json",
                        json.dumps({"status": "degraded"}).encode("utf-8"),
                    )
                    return
                self._write(200, "application/json", b'{"status":"ok"}')
                return

            if self.path == "/api/v1/basemap/config":
                payload = {
                    "schema_version": 1,
                    "configured_mode": "off",
                    "mode": "off",
                    "status": "disabled",
                    "fallback": "national",
                    "europe": {"available": False},
                }
                self._write(
                    200,
                    "application/json",
                    json.dumps(payload).encode("utf-8"),
                )
                return

            if self.path == "/api/v1/basemap/europe/0/0/0.mvt":
                self._write(404, "application/json", b'{"detail":"disabled"}')
                return

            self._write(404, "text/plain; charset=utf-8", b"not found")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    args.port_file.write_text(str(server.server_port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
