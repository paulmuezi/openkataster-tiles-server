#!/usr/bin/env python3
"""HTTP end-to-end check for project keys and origin-bound embed sessions.

Run inside the tiles container from the repository root:

    python tests/e2e_api_access.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


PORT = 18080
BASE_URL = f"http://127.0.0.1:{PORT}"
ADMIN_KEY = "e2e-admin-key"
PROJECT_KEY = "ok_free_e2e_project_key_0123456789"
PROJECT_HASH = hashlib.sha256(PROJECT_KEY.encode("utf-8")).hexdigest()
ALLOWED_ORIGIN = "https://integration.example"


def request(path: str, *, method: str = "GET", bearer: str = "", payload: dict | None = None, follow: bool = True):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=15) as response:
            raw = response.read()
            parsed = json.loads(raw) if raw and "application/json" in response.headers.get("Content-Type", "") else raw
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        parsed = json.loads(raw) if raw else None
        return exc.code, parsed, dict(exc.headers)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def expect_status(path: str, expected: int, **kwargs):
    status, payload, headers = request(path, **kwargs)
    if status != expected:
        raise AssertionError(f"{path}: expected {expected}, got {status}: {payload}")
    return payload, headers


def wait_until_ready(process: subprocess.Popen) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"test server exited with {process.returncode}")
        try:
            if request("/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise TimeoutError("test server did not become ready")


def main() -> int:
    temp_dir = Path(tempfile.mkdtemp(prefix="openkataster-api-e2e-"))
    env = os.environ.copy()
    env.update(
        {
            "OPENKATASTER_API_KEY_STORE": str(temp_dir / "api_keys.json"),
            "OPENKATASTER_API_USAGE_DB": str(temp_dir / "api_usage.sqlite"),
            "OPENKATASTER_TILE_ADMIN_KEYS": ADMIN_KEY,
            "OPENKATASTER_TILE_KEYS": "e2e-service-key",
            "OPENKATASTER_TILE_PRO_TOKENS": "e2e-pro-preview",
            "OPENKATASTER_EMBED_SESSION_SECRET": "e2e-session-secret-with-more-than-32-characters",
            "OPENKATASTER_EMBED_SESSION_TTL_SECONDS": "300",
            "OPENKATASTER_TILE_PUBLIC_BASE_URL": BASE_URL,
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "openkataster_tiles.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_until_ready(process)
        active_record = {
            "token_hash": PROJECT_HASH,
            "token_preview": "ok_free_e2e...",
            "status": "active",
            "plan": "free",
            "user_id": "e2e-user",
            "project_id": "e2e-project",
            "usage_subject": "project:e2e-project",
            "project_name": "E2E Project",
            "allowed_origins": [ALLOWED_ORIGIN],
            "scopes": ["embed:free", "search:basic", "feature:preview"],
            "monthly_limit": 5,
        }
        expect_status(
            "/internal/v1/api-keys/sync",
            200,
            method="POST",
            bearer=ADMIN_KEY,
            payload={"keys": [active_record]},
        )

        openapi, _ = expect_status("/openapi.json", 200)
        assert "OpenKatasterApiKey" in openapi["components"]["securitySchemes"]
        assert "/api/v1/embed/sessions" in openapi["paths"]
        assert "/internal/v1/api-keys/sync" not in openapi["paths"]
        embed_docs, _ = expect_status("/docs/embed", 200)
        assert b"openkataster:ready" in embed_docs
        viewer_session, _ = expect_status(
            "/internal/v1/viewer-sessions",
            200,
            method="POST",
            bearer=ADMIN_KEY,
            payload={"access": "pro", "subject": "e2e-pro-user", "name": "E2E Pro"},
        )
        assert viewer_session["token"]
        viewer_claims, _ = expect_status(
            "/api/v1/session?" + urllib.parse.urlencode({"token": viewer_session["token"]}),
            200,
        )
        assert viewer_claims["access"] == "pro"
        assert "feature:read" in viewer_claims["scopes"]

        expect_status("/api/v1/embed/sessions", 401, method="POST", payload={"origin": ALLOWED_ORIGIN})
        expect_status(
            "/api/v1/embed/sessions",
            403,
            method="POST",
            bearer=PROJECT_KEY,
            payload={"origin": "https://not-enabled.example"},
        )
        expect_status(
            "/api/v1/embed/sessions",
            403,
            method="POST",
            bearer=PROJECT_KEY,
            payload={"origin": ALLOWED_ORIGIN, "mode": "onoffice"},
        )
        session, _ = expect_status(
            "/api/v1/embed/sessions",
            200,
            method="POST",
            bearer=PROJECT_KEY,
            payload={"origin": ALLOWED_ORIGIN, "dataset": "deutschland"},
        )
        assert session["origin"] == ALLOWED_ORIGIN
        assert session["embed_url"].startswith(f"{BASE_URL}/embed/deutschland?")

        claims, _ = expect_status(
            "/api/v1/session?" + urllib.parse.urlencode({"session": session["session_token"]}),
            200,
        )
        assert claims["subject"] == "e2e-user"
        assert claims["access"] == "free"

        _, embed_headers = expect_status(
            "/embed/deutschland?" + urllib.parse.urlencode({"session": session["session_token"], "okParentOrigin": ALLOWED_ORIGIN}),
            200,
        )
        normalized_headers = {key.lower(): value for key, value in embed_headers.items()}
        assert normalized_headers.get("content-security-policy") == f"frame-ancestors {ALLOWED_ORIGIN}"

        expect_status("/api/v1/search/address?q=Hamburg&limit=1", 200, bearer=PROJECT_KEY)
        expect_status("/api/v1/suggest/places?q=Hamburg&limit=1", 200, bearer=PROJECT_KEY)
        usage, _ = expect_status(
            "/internal/v1/api-keys/usage",
            200,
            method="POST",
            bearer=ADMIN_KEY,
            payload={"token_hashes": [PROJECT_HASH]},
        )
        assert usage["usages"][PROJECT_HASH] >= 5
        expect_status(
            "/api/v1/embed/sessions",
            429,
            method="POST",
            bearer=PROJECT_KEY,
            payload={"origin": ALLOWED_ORIGIN},
        )

        disabled_record = {**active_record, "status": "disabled"}
        expect_status(
            "/internal/v1/api-keys/sync",
            200,
            method="POST",
            bearer=ADMIN_KEY,
            payload={"keys": [disabled_record]},
        )
        expect_status(
            "/api/v1/embed/sessions",
            401,
            method="POST",
            bearer=PROJECT_KEY,
            payload={"origin": ALLOWED_ORIGIN},
        )
        print("API key/embed E2E: OK")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        if process.returncode not in {0, -15, None} and process.stderr:
            print(process.stderr.read(), file=sys.stderr)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
