"""The 0.1 interface (cell tags, staging) and the transport's safety rules, against a fake API on localhost.

The fake records what it received so the tests can assert the request shapes the helper sends.
"""

import base64
import io
import math
import struct
import sys
from typing import ClassVar

import pytest

import rakiura
from rakiura._config import url_allowed

from .conftest import FakeApi, Request, serve

GOOD_KEY = "rknb_" + "a" * 64
STEP = {"id": "d-onb-1", "displayId": "ONB-001-D1", "name": "Step 1", "url": "u"}
ITEM_TAG = "ONB-001-D1/listen-intro-talk"
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
                {"key": "listen-intro-talk", "label": "Listen", "kind": "listen", "tag": ITEM_TAG, "checked": False},
            ],
        }
    ],
}
UNAUTHENTICATED = {"ok": False, "error": {"code": "unauthenticated", "message": "This key was not accepted."}}
NOT_FOUND = {"ok": False, "error": {"code": "not_found", "message": "no"}}


class Fake(FakeApi):
    received: ClassVar[list[Request]] = []
    mode = "ok"  # ok | already | forbidden | redirect | list

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {GOOD_KEY}"

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        self.record()
        if not self._auth_ok():
            self.reply(401, UNAUTHENTICATED)
        elif self.path == "/api/notebook/v1/me":
            self.reply(200, ME_PAYLOAD)
        else:
            self.reply(404, NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        body = self.read_json()
        self.record(body)
        if Fake.mode == "redirect":
            self.reply_raw(302, b"", {"Location": "/elsewhere"})
        elif Fake.mode == "list":
            self.reply_raw(200, b"[1, 2]")
        elif not self._auth_ok():
            self.reply(401, UNAUTHENTICATED)
        elif Fake.mode == "forbidden":
            message = 'You can\'t submit to "ONB-001-D1", so nothing was staged.'
            self.reply(403, {"ok": False, "error": {"code": "forbidden", "message": message}})
        elif self.path == "/api/notebook/v1/checkpoint":
            self.reply(
                200,
                {
                    "ok": True,
                    "deliverable": STEP,
                    "item": {"key": "listen-intro-talk", "label": "Listen", "tag": body.get("cellTag")},
                    "checked": body.get("status", "done") == "done",
                    "changed": Fake.mode != "already",
                },
            )
        elif self.path == "/api/notebook/v1/artifacts":
            kind = "file" if "fileText" in body else "link"
            self.reply(
                200,
                {
                    "ok": True,
                    "artifact": {"id": "a1", "title": body.get("title"), "kind": kind},
                    "deliverable": STEP,
                    "created": Fake.mode != "already",
                },
            )
        else:
            self.reply(404, NOT_FOUND)


@pytest.fixture(scope="module")
def server():
    yield from serve(Fake)


@pytest.fixture(autouse=True)
def fresh(server, monkeypatch, tmp_path):
    Fake.received.clear()
    Fake.mode = "ok"
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(tmp_path / "config" / "rakiura" / "credentials"))
    monkeypatch.setenv("RAKIURA_URL", server)


def test_login_from_env(monkeypatch, capsys):
    monkeypatch.setenv("RAKIURA_KEY", GOOD_KEY)
    assert rakiura.login() is True
    assert "key set" in capsys.readouterr().out


def test_login_rejects_non_notebook_key(capsys):
    assert rakiura.login("xyz_" + "b" * 64) is False
    assert "rknb_" in capsys.readouterr().out
    assert rakiura._session.key is None


def test_login_without_key_prints_one_line(monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "")
    assert rakiura.login() is False
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("rakiura:")


def test_me_lists_tags(capsys):
    rakiura.login(GOOD_KEY)
    payload = rakiura.me()
    assert payload is not None
    assert payload["lab"]["name"] == "Example Lab"
    assert ITEM_TAG in capsys.readouterr().out
    method, path, _, headers = Fake.received[-1]
    assert (method, path) == ("GET", "/api/notebook/v1/me")
    assert headers["Authorization"] == f"Bearer {GOOD_KEY}"
    assert headers["User-Agent"] == f"rakiura-py/{rakiura.__version__}"


def test_checkpoint_sends_cell_tag_and_status(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint(ITEM_TAG) is True
    _, path, body, _ = Fake.received[-1]
    assert path == "/api/notebook/v1/checkpoint"
    assert body == {"cellTag": ITEM_TAG, "status": "done"}
    assert 'ticked "Listen" on ONB-001-D1.' in capsys.readouterr().out


def test_checkpoint_idempotent_message(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "already"
    assert rakiura.checkpoint(ITEM_TAG) is True
    assert '"Listen" was already ticked.' in capsys.readouterr().out


def test_uncheck_sends_undone(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.uncheck(ITEM_TAG) is True
    assert Fake.received[-1][2]["status"] == "undone"
    assert 'unticked "Listen"' in capsys.readouterr().out


def test_checkpoint_bad_tag_never_calls_server(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint("no-slash") is False
    assert Fake.received == []
    assert "STEP-ID/item-key" in capsys.readouterr().out


def test_checkpoint_cell_decorator_runs_function_then_ticks():
    rakiura.login(GOOD_KEY)

    @rakiura.checkpoint_cell(ITEM_TAG)
    def work(x):
        return x * 2

    assert work(21) == 42
    assert Fake.received[-1][1] == "/api/notebook/v1/checkpoint"
    assert work.__name__ == "work"


def test_checkpoint_cell_decorator_propagates_own_exception():
    rakiura.login(GOOD_KEY)

    @rakiura.checkpoint_cell(ITEM_TAG)
    def boom():
        msg = "mine"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="mine"):
        boom()
    assert Fake.received == []  # no tick for a cell that failed


def test_revoked_key_prints_one_line_and_forgets_key(capsys):
    rakiura.login("rknb_" + "c" * 64)
    assert rakiura.checkpoint(ITEM_TAG) is False
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("rakiura: This key was not accepted")
    assert rakiura._session.key is None
    assert rakiura._session.last_error == out[-1]


def test_offline_prints_one_line_never_raises(capsys):
    rakiura.login(GOOD_KEY, url="http://127.0.0.1:9")  # nothing listens here
    assert rakiura.checkpoint(ITEM_TAG) is False
    out = capsys.readouterr().out.strip().splitlines()
    # The key-in-a-notebook warning, "key set", then exactly one failure line.
    assert len(out) == 3
    assert "could not reach" in out[-1]
    assert rakiura.me() is None


def test_forbidden_message_is_the_servers(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "forbidden"
    assert rakiura.stage_link("https://example.test/nb", "My notebook", step="ONB-001-D1") is False
    assert "nothing was staged" in capsys.readouterr().out


def test_stage_link_shape(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_link("https://example.test/nb.ipynb", "My notebook", step="ONB-001-D1", cell=ITEM_TAG)
    assert Fake.received[-1][2] == {
        "deliverableRef": "ONB-001-D1",
        "title": "My notebook",
        "url": "https://example.test/nb.ipynb",
        "cellTag": ITEM_TAG,
    }
    assert 'added "My notebook" to ONB-001-D1. It goes in when you submit: u' in capsys.readouterr().out


def test_stage_link_twice_says_already_added(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "already"
    assert rakiura.stage_link("https://example.test/nb.ipynb", "My notebook", step="ONB-001-D1") is True
    assert '"My notebook" was already added to ONB-001-D1.' in capsys.readouterr().out


def test_stage_link_refuses_non_http():
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


def test_stage_text_refuses_other_mime_types(capsys):
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_text("<p>", "page", step="ONB-001-D1", mime="text/html") is False
    assert Fake.received == []
    assert "text/plain or text/csv" in capsys.readouterr().out


class _Fig:
    """Stands in for a matplotlib Figure: savefig writes a tiny PNG header."""

    def __init__(self, size=100):
        self.size = size

    def savefig(self, buf, format="png", dpi=72, bbox_inches=None):  # noqa: A002 - matplotlib's keyword
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


def test_stage_figure_needs_savefig():
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(object(), "x", step="ONB-001-D1") is False
    assert Fake.received == []


def test_key_never_printed(capsys):
    rakiura.login(GOOD_KEY)
    rakiura.me()
    rakiura.checkpoint(ITEM_TAG)
    assert GOOD_KEY not in capsys.readouterr().out


def test_login_refuses_plain_http_to_another_host(capsys):
    assert rakiura.login(GOOD_KEY, url="http://labs.rakiura.co") is False
    assert "https://" in capsys.readouterr().out
    assert rakiura._session.key is None


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://labs.rakiura.co", True),
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://[::1]:3000", True),
        ("http://example.test", False),
        ("ftp://labs.rakiura.co", False),
        ("https://", False),
        ("labs.rakiura.co", False),
        ("http://[::1", False),
    ],
)
def test_only_https_or_local_http_is_allowed(url, allowed):
    assert url_allowed(url) is allowed


@pytest.mark.parametrize("without_requests", [False, True])
def test_redirect_is_not_followed(monkeypatch, capsys, without_requests):
    if without_requests:
        monkeypatch.setitem(sys.modules, "requests", None)  # import -> ImportError
    rakiura.login(GOOD_KEY)
    Fake.mode = "redirect"
    assert rakiura.checkpoint(ITEM_TAG) is False
    assert [r[1] for r in Fake.received] == ["/api/notebook/v1/checkpoint"]
    out = capsys.readouterr().out.strip().splitlines()
    assert "redirect" in out[-1]
    assert GOOD_KEY not in "\n".join(out)


def test_non_object_reply_never_raises(capsys):
    rakiura.login(GOOD_KEY)
    Fake.mode = "list"
    assert rakiura.checkpoint(ITEM_TAG) is False
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
    assert "empty" in out
    assert "plt.subplots()" in out
    assert out.count("\n") == 3  # the key warning, the login line, then exactly one line


def test_stage_figure_with_axes_is_sent():
    rakiura.login(GOOD_KEY)
    assert rakiura.stage_figure(_AxesFig(axes=["ax"]), "drawn", step="ONB-001-D1") is True
    assert Fake.received[-1][2]["title"] == "drawn"


def _png_width(png: bytes) -> int:
    """A PNG's width in pixels, from its IHDR chunk."""
    return struct.unpack(">I", png[16:20])[0]


def test_busy_figure_stays_legible_under_the_cap():
    # A busy four-panel figure is ~97 KB as a
    # full-colour PNG at 72 dpi. It used to be shrunk to 36 dpi to fit 50 KB,
    # which blurs every label; the palette copy keeps it at 72 dpi or better.
    matplotlib = pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # only after the backend is chosen

    fig, axes = plt.subplots(4, 1, figsize=(9, 11), sharex=True)
    xs = [i / 50 for i in range(400)]
    for row, ax in enumerate(axes):
        ax.grid(visible=True, alpha=0.25, linestyle=":")
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
    sent = Fake.received[-1][2]["fileText"]
    assert len(sent) <= rakiura.FILE_TEXT_MAX_BYTES
    assert _png_width(base64.b64decode(sent)) >= _png_width(full.getvalue())  # never shrunk below 72 dpi
    plt.close(fig)
