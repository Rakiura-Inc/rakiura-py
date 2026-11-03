"""Unit tests with the HTTP layer mocked: no server at all, so they check exactly what would go on the wire."""

import json
import sys
import types

import pytest

import rakiura
from rakiura import _credentials, _runs, _transport
from rakiura._runs import run_phrase
from rakiura._steps import clean_numbers

GOOD_KEY = "rknb_" + "a" * 64

PUBLIC_API = {
    "RakiuraError",
    "checkpoint",
    "checkpoint_cell",
    "done",
    "login",
    "logout",
    "me",
    "share_notebook",
    "stage_figure",
    "stage_link",
    "stage_text",
    "start",
    "submit",
    "uncheck",
}


class Wire:
    """Stands in for ``_transport._send``: records each request and answers from a queue."""

    def __init__(self, *replies: tuple[int, object]) -> None:
        self.replies = list(replies)
        self.sent: list[dict] = []

    def __call__(self, method, url, headers, data):
        body = json.loads(data) if data else None
        self.sent.append({"method": method, "url": url, "headers": headers, "body": body})
        status, payload = self.replies.pop(0)
        return status, payload if isinstance(payload, str) else json.dumps(payload)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Mocks the HTTP layer; the default Rakiura address, and a throwaway credentials file."""
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(tmp_path / "credentials"))
    monkeypatch.delenv("RAKIURA_URL", raising=False)

    def install(*replies):
        fake = Wire(*replies)
        monkeypatch.setattr(_transport, "_send", fake)
        return fake

    return install


# ── the public surface existing notebooks rely on ────────────────────────────


def test_public_api_is_unchanged():
    assert set(rakiura.__all__) == PUBLIC_API
    assert all(callable(getattr(rakiura, name)) for name in PUBLIC_API)
    assert rakiura.DEFAULT_URL == "https://labs.rakiura.co"
    assert rakiura.KEY_PREFIX == "rknb_"
    assert rakiura.FILE_TEXT_MAX_BYTES == 50 * 1024
    assert rakiura.MAX_NUMBERS == 20


def test_version_matches_the_package_metadata():
    from importlib.metadata import version

    assert rakiura.__version__ == version("rakiura") == "0.3.1"


def test_rakiura_error_keeps_its_0_1_signature():
    err = rakiura.RakiuraError("rakiura: no", "forbidden")
    assert str(err) == "rakiura: no"
    assert err.code == "forbidden"
    assert err.status is None


# ── the wire ─────────────────────────────────────────────────────────────────


def test_calls_go_to_labs_rakiura_co_over_https_with_the_key(wire):
    sent = wire((200, {"ok": True, "lab": {"name": "Lab"}, "member": {"name": "Me"}, "deliverables": []}))
    rakiura.login(GOOD_KEY)
    payload = rakiura.me(show=False)
    assert payload is not None
    assert payload["lab"]["name"] == "Lab"
    (request,) = sent.sent
    assert request["method"] == "GET"
    assert request["url"] == "https://labs.rakiura.co/api/notebook/v1/me"
    assert request["headers"]["Authorization"] == f"Bearer {GOOD_KEY}"
    assert request["headers"]["Accept"] == "application/json"
    assert request["body"] is None


def test_a_json_body_says_so(wire):
    sent = wire((200, {"ok": True, "item": {"label": "L"}, "deliverable": {"displayId": "S"}, "changed": True}))
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint("S/item") is True
    assert sent.sent[0]["headers"]["Content-Type"] == "application/json"
    assert sent.sent[0]["body"] == {"cellTag": "S/item", "status": "done"}


def test_start_refuses_plain_http_to_another_host(wire, capsys):
    sent = wire()
    assert rakiura.start(notebook="x.ipynb", url="http://labs.rakiura.co") is False
    assert sent.sent == []
    assert "must start with https://" in capsys.readouterr().out


def test_an_unreachable_server_is_one_line_naming_only_the_error_type(monkeypatch, wire, capsys):
    def offline(*_args):
        msg = f"secret detail {GOOD_KEY}"
        raise ConnectionError(msg)

    wire()
    monkeypatch.setattr(_transport, "_send", offline)
    rakiura.login(GOOD_KEY)
    assert rakiura.checkpoint("S/item") is False
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == (
        "rakiura: could not reach https://labs.rakiura.co (ConnectionError). "
        "Your work is fine; tick the box on the step page instead."
    )
    assert GOOD_KEY not in out


def test_a_rakiura_without_device_login_asks_for_a_key(wire, monkeypatch):
    # An older Rakiura answers its own HTML 404 page: fall back to a hidden prompt.
    wire((404, "<html>Not found</html>"))
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": GOOD_KEY)
    assert rakiura.login() is True
    assert rakiura._session.key_source == "prompt"


def test_a_device_reply_with_a_non_https_link_is_refused(wire, capsys):
    wire((200, {"ok": True, "deviceCode": "c", "verificationUri": "http://evil.example/allow"}))
    assert rakiura.login() is False
    assert "unexpected reply" in capsys.readouterr().out


def test_a_device_key_without_the_prefix_is_never_used(wire, monkeypatch, capsys):
    monkeypatch.setattr(rakiura._login.time, "sleep", lambda _s: None)
    start = {"ok": True, "deviceCode": "c", "verificationUri": "https://labs.rakiura.co/oauth/device", "interval": 1}
    wire((200, start), (200, {"ok": True, "key": "not-a-notebook-key"}))
    assert rakiura.login() is False
    assert rakiura._session.key is None
    assert "not-a-notebook-key" not in capsys.readouterr().out


class _FakeResponse:
    status_code = 200
    text = '{"ok": true}'


def test_requests_is_told_not_to_redirect_and_not_to_use_netrc(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(request=request))
    assert _transport._send("GET", "https://labs.rakiura.co/x", {"A": "b"}, None) == (200, '{"ok": true}')
    ((method, url, kwargs),) = calls
    assert (method, url) == ("GET", "https://labs.rakiura.co/x")
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == 15
    assert kwargs["headers"] == {"A": "b"}
    marker = object()
    assert kwargs["auth"](marker) is marker  # a no-op auth: the key's header is never replaced from ~/.netrc


# ── small pieces ─────────────────────────────────────────────────────────────


class _NumpyScalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


def test_numpy_scalars_become_plain_numbers():
    assert clean_numbers({"a": _NumpyScalar(1.5), "b": _NumpyScalar(2), "c": "text", "d": False, "e": 10**400}) == {
        "a": 1.5,
        "b": 2,
        "c": "text",
        "d": False,
        "e": 10**400,
    }


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), _NumpyScalar([1, 2]), None, b"bytes"])
def test_anything_but_a_single_finite_value_is_refused(bad):
    assert clean_numbers({"x": bad}) is None


def test_more_than_twenty_numbers_are_refused():
    assert clean_numbers({f"n{i}": i for i in range(20)}) is not None
    assert clean_numbers({f"n{i}": i for i in range(21)}) is None


@pytest.mark.parametrize(
    ("info", "verdict", "phrase"),
    [
        ({"environment": "local"}, None, "running on this computer, no git checkout"),
        ({"environment": "colab", "commitSha": "8f7378c0aa"}, "pinned", "running the pinned commit 8f7378c in Colab"),
        (
            {"environment": "binder", "commitSha": "8f7378c0aa", "dirty": True},
            "fork_of_pin",
            "running your fork at 8f7378c (edited) on Binder",
        ),
        ({"environment": "elsewhere", "commitSha": "abcdef12"}, None, "running commit abcdef1 here"),
    ],
)
def test_run_phrase(info, verdict, phrase):
    assert run_phrase(info, verdict) == phrase


def test_a_pushed_unedited_commit_gets_a_github_link(monkeypatch, capsys):
    sha = "a" * 40
    monkeypatch.setattr(_runs, "toplevel", lambda: "/repo")
    monkeypatch.setattr(_runs, "git", lambda args, cwd=None: "origin/main" if args[:2] == ["branch", "-r"] else None)
    rakiura._session.run_info = {"repo": "owner/name", "commitSha": sha, "notebookPath": "a/b.ipynb", "dirty": False}
    assert rakiura.share_notebook() == f"https://github.com/owner/name/blob/{sha}/a/b.ipynb"
    assert capsys.readouterr().out == f"rakiura: https://github.com/owner/name/blob/{sha}/a/b.ipynb\n"


@pytest.mark.parametrize(
    "info",
    [
        {"repo": "owner/name", "commitSha": "a" * 40, "notebookPath": "b.ipynb", "dirty": True},
        {"repo": "gitlab.com/owner/name", "commitSha": "a" * 40, "notebookPath": "b.ipynb"},
        {"repo": "owner/name", "notebookPath": "b.ipynb"},
    ],
)
def test_no_github_link_for_edited_non_github_or_uncommitted_runs(info):
    assert _runs.github_permalink(info) is None


@pytest.mark.parametrize(
    ("expires_at", "expired"),
    [
        ("2001-01-01T00:00:00.000Z", True),
        ("2999-01-01T00:00:00+00:00", False),
        ("2001-01-01T00:00:00", False),  # no zone: not known to have passed
        ("not a time", False),
        (None, False),
    ],
)
def test_expiry_is_only_trusted_when_readable(expires_at, expired):
    assert _credentials._expired(expires_at) is expired


def test_quiet_prints_nothing(wire, capsys):
    wire()
    assert rakiura.login("not-a-key", quiet=True) is False
    assert capsys.readouterr().out == ""
    assert (rakiura._session.last_error or "").startswith("rakiura: that does not look like a notebook key")
