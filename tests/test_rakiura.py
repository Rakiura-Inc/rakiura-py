"""rakiura package tests: a fake /api/notebook/v1 server on localhost.

No network, no real key. The fake records what it received so the tests can
assert the request shapes the helper sends.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rakiura  # noqa: E402

GOOD_KEY = "rknb_" + "a" * 64
ME_PAYLOAD = {
    "ok": True,
    "lab": {"id": "lab-1", "name": "Example Lab"},
    "member": {"researcherId": "r-1", "name": "Test Member"},
    "key": {"id": "k1", "label": "laptop", "expiresAt": "2027-01-01T00:00:00.000Z"},
    "deliverables": [
        {
            "id": "d-onb-1",
            "displayId": "ONB-001-D1",
            "name": "Step 1",
            "projectId": "p-onb-001",
            "projectTitle": "Onboarding",
            "canStageArtifacts": True,
            "url": "https://labs.example.test/example-lab/projects/p-onb-001/deliverables/d-onb-1",
            "items": [
                {"key": "listen-intro-talk", "label": "Listen", "kind": "listen", "tag": "ONB-001-D1/listen-intro-talk", "checked": False},
            ],
        }
    ],
}


class Fake(BaseHTTPRequestHandler):
    received: list = []
    mode = "ok"

    def log_message(self, *a):  # quiet
        pass

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Bearer {GOOD_KEY}"

    def do_GET(self):
        Fake.received.append(("GET", self.path, None, dict(self.headers)))
        if not self._auth_ok():
            return self._reply(401, {"ok": False, "error": {"code": "unauthenticated", "message": "This key was not accepted."}})
        if self.path == "/api/notebook/v1/me":
            return self._reply(200, ME_PAYLOAD)
        self._reply(404, {"ok": False, "error": {"code": "not_found", "message": "no"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        Fake.received.append(("POST", self.path, body, dict(self.headers)))
        if Fake.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if Fake.mode == "list":
            data = b"[1, 2]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not self._auth_ok():
            return self._reply(401, {"ok": False, "error": {"code": "unauthenticated", "message": "This key was not accepted."}})
        if Fake.mode == "forbidden":
            return self._reply(403, {"ok": False, "error": {"code": "forbidden", "message": "You can't submit to \"ONB-001-D1\", so nothing was staged."}})
        if self.path == "/api/notebook/v1/checkpoint":
            changed = Fake.mode != "already"
            return self._reply(200, {
                "ok": True,
                "deliverable": {"id": "d-onb-1", "displayId": "ONB-001-D1", "name": "Step 1", "url": "u"},
                "item": {"key": "listen-intro-talk", "label": "Listen", "tag": body.get("cellTag")},
                "checked": body.get("status", "done") == "done",
                "changed": changed,
            })
        if self.path == "/api/notebook/v1/artifacts":
            return self._reply(200, {
                "ok": True,
                "artifact": {"id": "a1", "title": body.get("title"), "kind": "file" if "fileText" in body else "link"},
                "deliverable": {"id": "d-onb-1", "displayId": "ONB-001-D1", "name": "Step 1", "url": "u"},
                "created": Fake.mode != "already",
            })
        self._reply(404, {"ok": False, "error": {"code": "not_found", "message": "no"}})


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def fresh(server, monkeypatch, tmp_path):
    Fake.received.clear()
    Fake.mode = "ok"
    # Never the real ~/.config/rakiura, never a real browser.
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(tmp_path / "config" / "rakiura" / "credentials"))
    monkeypatch.setenv("RAKIURA_NO_BROWSER", "1")
    rakiura.logout(forget=False)
    rakiura._session.last_error = None
    monkeypatch.delenv("RAKIURA_KEY", raising=False)
    monkeypatch.setenv("RAKIURA_URL", server)
    yield


def test_login_from_env(monkeypatch, capsys):
    monkeypatch.setenv("RAKIURA_KEY", GOOD_KEY)
    assert rakiura.login() is True
    assert "key set" in capsys.readouterr().out


def test_login_rejects_non_notebook_key(capsys):
    assert rakiura.login("xyz_" + "b" * 64) is False
    assert "rknb_" in capsys.readouterr().out
    assert rakiura._session.key is None


def test_login_without_key_prints_one_line(monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    assert rakiura.login() is False
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("rakiura:")


def test_me_lists_tags(capsys):
    rakiura.login(GOOD_KEY)
    payload = rakiura.me()
    assert payload["lab"]["name"] == "Example Lab"
    out = capsys.readouterr().out
    assert "ONB-001-D1/listen-intro-talk" in out
    method, path, _, headers = Fake.received[-1]
    assert (method, path) == ("GET", "/api/notebook/v1/me")
    assert headers["Authorization"] == f"Bearer {GOOD_KEY}"
    assert headers["User-Agent"].startswith("rakiura-py/")


def test_checkpoint_sends_cell_tag_and_status(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is True
    _, path, body, _ = Fake.received[-1]
    assert path == "/api/notebook/v1/checkpoint"
    assert body == {"cellTag": "ONB-001-D1/listen-intro-talk", "status": "done"}
    assert "ticked" in capsys.readouterr().out


def test_checkpoint_idempotent_message(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "already"
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is True
    assert "already ticked" in capsys.readouterr().out


def test_uncheck_sends_undone():
    rakiura.login(GOOD_KEY)
    assert rakiura.uncheck("ONB-001-D1/listen-intro-talk") is True
    assert Fake.received[-1][2]["status"] == "undone"


def test_checkpoint_bad_tag_never_calls_server(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint("no-slash") is False
    assert Fake.received == []
    assert "STEP-ID/item-key" in capsys.readouterr().out


def test_checkpoint_cell_decorator_runs_function_then_ticks():
    rakiura.login(GOOD_KEY)

    @rakiura.checkpoint_cell("ONB-001-D1/listen-intro-talk")
    def work(x):
        return x * 2

    assert work(21) == 42
    assert Fake.received[-1][1] == "/api/notebook/v1/checkpoint"


def test_checkpoint_cell_decorator_propagates_own_exception():
    rakiura.login(GOOD_KEY)

    @rakiura.checkpoint_cell("ONB-001-D1/listen-intro-talk")
    def boom():
        raise ValueError("mine")

    with pytest.raises(ValueError):
        boom()
    assert Fake.received == []  # no tick for a cell that failed


def test_revoked_key_prints_one_line_and_forgets_key(capsys):
    rakiura.login("rknb_" + "c" * 64)
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is False
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("rakiura: This key was not accepted")
    assert rakiura._session.key is None


def test_offline_prints_one_line_never_raises(monkeypatch, capsys):
    rakiura.login(GOOD_KEY, url="http://127.0.0.1:9")  # nothing listens here
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is False
    out = capsys.readouterr().out.strip().splitlines()
    # The key-in-a-notebook warning, "key set", then exactly one failure line.
    assert len(out) == 3 and "could not reach" in out[-1]
    assert rakiura.me() is None


def test_forbidden_message_is_the_servers(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "forbidden"
    assert rakiura.stage_link("https://example.test/nb", "My notebook", step="ONB-001-D1") is False
    assert "nothing was staged" in capsys.readouterr().out


def test_stage_link_shape():
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_link("https://example.test/nb.ipynb", "My notebook", step="ONB-001-D1", cell="ONB-001-D1/listen-intro-talk")
    body = Fake.received[-1][2]
    assert body == {
        "deliverableRef": "ONB-001-D1",
        "title": "My notebook",
        "url": "https://example.test/nb.ipynb",
        "cellTag": "ONB-001-D1/listen-intro-talk",
    }


def test_stage_link_refuses_non_http(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_link("file:///tmp/x", "x", step="ONB-001-D1") is False
    assert Fake.received == []


def test_stage_text_caps_at_50kb(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_text("a" * (50 * 1024 + 1), "big", step="ONB-001-D1") is False
    assert Fake.received == []
    assert "50 KB" in capsys.readouterr().out
    assert rakiura.stage_text("a,b\n1,2\n", "small", step="ONB-001-D1", mime="text/csv") is True
    assert Fake.received[-1][2]["mimeType"] == "text/csv"


class _Fig:
    """Stands in for a matplotlib Figure: savefig writes a tiny PNG header."""

    def __init__(self, size=100):
        self.size = size

    def savefig(self, buf, format="png", dpi=72, bbox_inches=None):
        buf.write(b"\x89PNG\r\n\x1a\n" + b"\0" * max(0, self.size - 8))


def test_stage_figure_sends_base64_png():
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(_Fig(), "My plot", step="ONB-001-D1") is True
    body = Fake.received[-1][2]
    assert body["mimeType"] == "image/png"
    assert base64.b64decode(body["fileText"]).startswith(b"\x89PNG")
    assert body["deliverableRef"] == "ONB-001-D1"


def test_stage_figure_too_big_prints_one_line(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(_Fig(size=60 * 1024), "huge", step="ONB-001-D1") is False
    assert Fake.received == []
    assert "stage_link" in capsys.readouterr().out


def test_stage_figure_needs_savefig(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(object(), "x", step="ONB-001-D1") is False
    assert Fake.received == []


def test_key_never_printed(capsys):
    rakiura.login(GOOD_KEY)
    rakiura.me()
    rakiura.checkpoint("ONB-001-D1/listen-intro-talk")
    assert GOOD_KEY not in capsys.readouterr().out


def test_login_refuses_plain_http_to_another_host(capsys):
    assert rakiura.login(GOOD_KEY, url="http://labs.rakiura.co") is False
    assert "https://" in capsys.readouterr().out
    assert rakiura._session.key is None


def test_login_allows_https_and_local_http():
    assert rakiura._url_allowed("https://labs.rakiura.co") is True
    assert rakiura._url_allowed("http://localhost:3000") is True
    assert rakiura._url_allowed("http://127.0.0.1:3000") is True
    assert rakiura._url_allowed("http://example.test") is False
    assert rakiura._url_allowed("ftp://labs.rakiura.co") is False


@pytest.mark.parametrize("without_requests", [False, True])
def test_redirect_is_not_followed(monkeypatch, capsys, without_requests):
    if without_requests:
        monkeypatch.setitem(sys.modules, "requests", None)  # import -> ImportError
    rakiura.login(GOOD_KEY)
    Fake.mode = "redirect"
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is False
    assert [r[1] for r in Fake.received] == ["/api/notebook/v1/checkpoint"]
    out = capsys.readouterr().out.strip().splitlines()
    assert "redirect" in out[-1] and GOOD_KEY not in "\n".join(out)


def test_non_object_reply_never_raises(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "list"
    assert rakiura.checkpoint("ONB-001-D1/listen-intro-talk") is False
    assert "unexpected reply" in capsys.readouterr().out


class _AxesFig(_Fig):
    """A stub figure that, like matplotlib's, lists its axes."""

    def __init__(self, axes, size=100):
        super().__init__(size)
        self.axes = axes


def test_stage_figure_refuses_an_empty_figure(capsys):
    # plt.gcf() after plt.show() is a new figure with no axes: staging it
    # used to report success and put a blank image in the tray.
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(_AxesFig(axes=[]), "blank", step="ONB-001-D1") is False
    assert Fake.received == []
    out = capsys.readouterr().out
    assert "empty" in out and "plt.subplots()" in out
    assert out.count("\n") == 3  # the key warning, the login line, then exactly one line


def test_stage_figure_with_axes_is_sent():
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(_AxesFig(axes=["ax"]), "drawn", step="ONB-001-D1") is True
    assert Fake.received[-1][2]["title"] == "drawn"


def test_busy_figure_stays_legible_under_the_cap():
    # A busy four-panel figure is ~97 KB as a
    # full-colour PNG at 72 dpi. It used to be shrunk to 36 dpi to fit 50 KB,
    # which blurs every label; the palette copy keeps it at 72 dpi or better.
    matplotlib = pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    matplotlib.use("Agg")
    import struct

    import matplotlib.pyplot as plt

    import math

    fig, axes = plt.subplots(4, 1, figsize=(9, 11), sharex=True)
    xs = [i / 50 for i in range(400)]
    for row, ax in enumerate(axes):
        ax.grid(True, alpha=0.25, linestyle=":")
        for series in range(4):
            ys = [math.sin(x * (series + 1) + row) * (series + 1) + x for x in xs]
            ax.plot(xs, ys, lw=2, ls=("-", "--")[series % 2], label=f"Star {series} mass")
        ax.legend(loc="upper left", ncol=4, fontsize=8)
        ax.set_ylabel("value [unit]")
    axes[-1].set_xlabel("Time [Myr]")

    full = io.BytesIO()
    fig.savefig(full, format="png", dpi=72, bbox_inches="tight")
    assert len(base64.b64encode(full.getvalue())) > rakiura.FILE_TEXT_MAX_BYTES  # the hard case

    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(fig, "Busy figure", step="ONB-001-D1") is True
    png = base64.b64decode(Fake.received[-1][2]["fileText"])
    assert len(Fake.received[-1][2]["fileText"]) <= rakiura.FILE_TEXT_MAX_BYTES
    width = struct.unpack(">I", png[16:20])[0]
    width_at_72_dpi = struct.unpack(">I", full.getvalue()[16:20])[0]
    assert width >= width_at_72_dpi  # never shrunk below 72 dpi
    plt.close(fig)
