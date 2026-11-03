"""Shared test helpers: a fake Rakiura notebook API on 127.0.0.1, and a clean session for every test.

No network, no real key, never the real ~/.config, never a real browser.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

import rakiura
from rakiura._session import Session

# Everything the helper reads from the environment: no test sees the developer's own.
HELPER_VARIABLES = (
    "RAKIURA_KEY",
    "RAKIURA_CREDENTIALS",
    "XDG_CONFIG_HOME",
    "BINDER_SERVICE_HOST",
    "BINDER_REPO_URL",
    "BINDER_LAUNCH_HOST",
    "BINDER_REF_URL",
    "COLAB_RELEASE_TAG",
    "JPY_SESSION_NAME",
)

Request = tuple[str, str, Any, dict[str, str]]


class FakeApi(BaseHTTPRequestHandler):
    """Base for a fake API: records every request as (method, path, body, headers) and answers JSON."""

    received: ClassVar[list[Request]] = []

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass  # quiet

    def reply(self, status: int, body: object) -> None:
        self.reply_raw(status, json.dumps(body).encode())

    def reply_raw(self, status: int, data: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def record(self, body: object = None) -> None:
        type(self).received.append((self.command, self.path, body, dict(self.headers)))

    def read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")


def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    """Run ``handler`` on a free local port for as long as the generator is open; yields its address."""
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def clean_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts disconnected, with no run, and without the developer's environment."""
    for name in HELPER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAKIURA_NO_BROWSER", "1")
    # Reset the one session object in place: every module holds a reference to it.
    vars(rakiura._session).update(vars(Session()))
