"""rakiura 0.2 tests: one-click device login, the saved connection, run
provenance, done() and submit(), against a fake /api/notebook/v1 on localhost.

No network, no real key, never the real ~/.config, never a real browser.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rakiura  # noqa: E402

DEVICE_KEY = "rknb_" + "d" * 64
DEVICE_CODE = "rknd_" + "e" * 64

# Exactly what a run may report. Anything else leaving the computer is a bug.
RUN_FIELDS = {"notebookPath", "repo", "commitSha", "dirty", "environment", "helperVersion", "pythonVersion"}


class Fake(BaseHTTPRequestHandler):
    received: list = []
    # Device login: how many polls answer "pending" before the outcome.
    pending_polls = 1
    outcome = "approve"  # approve | deny | expire | slow
    checkpoint_mode = "ok"  # ok | unmapped
    token_issued = 0

    def log_message(self, *a):
        pass

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Fake.received.append(("GET", self.path, None, dict(self.headers)))
        self._reply(404, {"ok": False, "error": {"code": "not_found", "message": "no"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        Fake.received.append(("POST", self.path, body, dict(self.headers)))
        port = self.server.server_port
        if self.path == "/api/notebook/v1/device":
            return self._reply(200, {
                "ok": True,
                "deviceCode": DEVICE_CODE,
                "userCode": "WXYZ-BCDF",
                "verificationUri": f"http://127.0.0.1:{port}/oauth/device",
                "verificationUriComplete": f"http://127.0.0.1:{port}/oauth/device?code=WXYZ-BCDF",
                "expiresIn": 600,
                "interval": 1,
            })
        if self.path == "/api/notebook/v1/device/token":
            assert body == {"deviceCode": DEVICE_CODE}
            assert "Authorization" not in self.headers
            polls = sum(1 for r in Fake.received if r[1] == "/api/notebook/v1/device/token")
            if Fake.outcome == "slow" and polls == 1:
                return self._reply(400, {"ok": False, "error": {"code": "slow_down", "message": "slow"}})
            if polls <= Fake.pending_polls:
                return self._reply(400, {"ok": False, "error": {"code": "authorization_pending", "message": "wait"}})
            if Fake.outcome == "deny":
                return self._reply(400, {"ok": False, "error": {"code": "access_denied", "message": "The sign-in was cancelled in Rakiura."}})
            if Fake.outcome == "expire":
                return self._reply(400, {"ok": False, "error": {"code": "expired_token", "message": "The sign-in code expired. Run rakiura.login() again for a new one."}})
            if Fake.token_issued:
                return self._reply(400, {"ok": False, "error": {"code": "invalid_grant", "message": "That sign-in was already used."}})
            Fake.token_issued += 1
            return self._reply(200, {
                "ok": True,
                "key": DEVICE_KEY,
                "expiresAt": "2099-01-01T00:00:00.000Z",
                "label": "Connected from your computer, Oct 6",
                "lab": {"id": "lab-1", "name": "Example Lab"},
                "member": {"name": "Test Member"},
            })
        if self.headers.get("Authorization") != f"Bearer {DEVICE_KEY}":
            return self._reply(401, {"ok": False, "error": {"code": "unauthenticated", "message": "This key was not accepted."}})
        if self.path == "/api/notebook/v1/runs":
            return self._reply(200, {
                "ok": True,
                "run": {"id": "run-1", "pinVerdict": "fork_of_pin"},
                "lab": {"id": "lab-1", "name": "Example Lab"},
                "member": {"name": "Test Member"},
                "notebook": {"path": body.get("notebookPath"), "title": "Tutorial 1"},
                "parts": [{"part": "part-2", "step": {"displayId": "ONB-002-D4", "name": "Notebook 1", "url": "u"}}],
            })
        if self.path == "/api/notebook/v1/checkpoint":
            if Fake.checkpoint_mode == "unmapped":
                return self._reply(404, {"ok": False, "error": {"code": "unmapped", "message": "\"part-9\" in Tutorial 1 isn't linked to a step yet, so nothing was logged."}})
            return self._reply(200, {
                "ok": True,
                "deliverable": {"id": "d4", "displayId": "ONB-002-D4", "name": "Notebook 1", "url": "u"},
                "item": {"key": body.get("checkpoint"), "label": "Part 2", "tag": "ONB-002-D4/part-2"},
                "checked": True,
                "changed": True,
                "progress": {"done": 1, "total": 3},
            })
        if self.path == "/api/notebook/v1/artifacts":
            return self._reply(200, {
                "ok": True,
                "artifact": {"id": "a1", "title": body.get("title"), "kind": "file"},
                "deliverable": {"id": "d4", "displayId": "ONB-002-D4", "name": "Notebook 1", "url": "u"},
                "created": True,
            })
        if self.path == "/api/notebook/v1/submit":
            return self._reply(200, {
                "ok": True,
                "submission": {"id": "s1", "version": 1},
                "deliverable": {"id": "d4", "displayId": "ONB-002-D4", "name": "Notebook 1", "url": "https://labs.example.test/s"},
                "attached": 2,
            })
        self._reply(404, {"ok": False, "error": {"code": "not_found", "message": "no"}})


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def creds(tmp_path):
    return tmp_path / "home" / ".config" / "rakiura" / "credentials"


@pytest.fixture(autouse=True)
def fresh(server, monkeypatch, creds, tmp_path):
    Fake.received.clear()
    Fake.pending_polls = 1
    Fake.outcome = "approve"
    Fake.checkpoint_mode = "ok"
    Fake.token_issued = 0
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(creds))
    monkeypatch.setenv("RAKIURA_NO_BROWSER", "1")
    monkeypatch.setenv("RAKIURA_URL", server)
    for var in ("RAKIURA_KEY", "BINDER_SERVICE_HOST", "BINDER_REPO_URL", "BINDER_LAUNCH_HOST", "BINDER_REF_URL", "COLAB_RELEASE_TAG", "JPY_SESSION_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(rakiura.time, "sleep", lambda s: None)
    # Outside any git repository unless a test makes one.
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    rakiura.logout(forget=False)
    rakiura._session.last_error = None
    yield


def posts(path):
    return [r for r in Fake.received if r[0] == "POST" and r[1] == f"/api/notebook/v1/{path}"]


# ── device login ─────────────────────────────────────────────────────────────


def test_device_login_saves_key_0600_outside_repo_and_never_prints_it(capsys, creds):
    assert rakiura.login() is True
    out = capsys.readouterr().out
    assert "WXYZ-BCDF" in out and "oauth/device?code=WXYZ-BCDF" in out
    assert "connected as Test Member in Example Lab" in out
    assert DEVICE_KEY not in out and DEVICE_CODE not in out
    assert rakiura._session.key == DEVICE_KEY
    saved = json.loads(creds.read_text())
    assert saved["keys"][rakiura._session.url]["key"] == DEVICE_KEY
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(creds.parent).st_mode) == 0o700


def test_saved_key_is_reused_without_a_new_sign_in(capsys):
    assert rakiura.login() is True
    rakiura.logout(forget=False)
    Fake.received.clear()
    assert rakiura.login() is True
    assert Fake.received == []  # no device login: the saved connection
    assert rakiura._session.key_source == "saved"


def test_saved_key_is_only_sent_to_the_address_it_came_from(server, creds):
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"version": 1, "keys": {"https://elsewhere.example": {"key": DEVICE_KEY}}}))
    assert rakiura.login() is True
    assert posts("device"), "a key saved for another address must not be used here"


def test_a_failed_sign_in_at_another_address_never_keeps_the_old_key(server):
    assert rakiura.login() is True
    assert rakiura._session.key == DEVICE_KEY
    other = server.replace("127.0.0.1", "localhost")  # another address, as far as keys go
    Fake.outcome = "deny"
    assert rakiura.login(url=other) is False
    assert rakiura._session.url == other
    assert rakiura._session.key is None, "the key from the first address must not be sent to the second"


def test_denied_sign_in_returns_false_with_one_line(capsys, creds):
    Fake.outcome = "deny"
    assert rakiura.login() is False
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "rakiura: The sign-in was cancelled in Rakiura."
    assert rakiura._session.key is None and not creds.exists()


def test_expired_sign_in_returns_false(capsys):
    Fake.outcome = "expire"
    assert rakiura.login() is False
    assert "expired" in capsys.readouterr().out


def test_slow_down_backs_off(monkeypatch):
    waits = []
    monkeypatch.setattr(rakiura.time, "sleep", lambda s: waits.append(s))
    Fake.outcome = "slow"
    assert rakiura.login() is True
    assert waits[0] == 1 and max(waits) >= 6


def test_remember_false_keeps_the_key_in_memory_only(creds):
    assert rakiura.login(remember=False) is True
    assert not creds.exists()


def test_never_saves_inside_a_git_repository(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    inside = repo / ".rakiura" / "credentials"
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(inside))
    assert rakiura.login() is True
    assert not inside.exists()
    assert "not saving" in capsys.readouterr().out


def test_never_saves_into_a_dotfiles_repository_even_from_outside_it(tmp_path, monkeypatch, capsys):
    # ~/.config tracked in a dotfiles repository: the kernel's own folder is
    # NOT a checkout, but the credentials file would be inside one.
    home = tmp_path / "dotfiles-home"
    (home / ".config").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    monkeypatch.delenv("RAKIURA_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    assert rakiura.login() is True
    assert not (home / ".config" / "rakiura" / "credentials").exists()
    assert "not saving" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_an_existing_loose_config_folder_is_tightened_to_0700(tmp_path, monkeypatch):
    config = tmp_path / "plain-home" / ".config"
    folder = config / "rakiura"
    folder.mkdir(parents=True)
    os.chmod(folder, 0o755)
    monkeypatch.delenv("RAKIURA_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    assert rakiura.login() is True
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(folder / "credentials").st_mode) == 0o600
    assert not [p for p in os.listdir(folder) if ".tmp-" in p]


def test_env_key_wins_and_is_never_saved(monkeypatch, creds):
    monkeypatch.setenv("RAKIURA_KEY", DEVICE_KEY)
    assert rakiura.login() is True
    assert rakiura._session.key_source == "env"
    assert not posts("device") and not creds.exists()


def test_colab_secret_is_read(monkeypatch):
    colab = types.ModuleType("google.colab")
    userdata = types.SimpleNamespace(get=lambda name: DEVICE_KEY if name == "RAKIURA_KEY" else None)
    colab.userdata = userdata  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    assert rakiura.login() is True
    assert rakiura._session.key_source == "colab"


def test_revoked_saved_key_is_forgotten_on_this_computer(creds, capsys):
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"version": 1, "keys": {rakiura._base_url(None): {"key": "rknb_" + "f" * 64}}}))
    assert rakiura.login() is True and rakiura._session.key_source == "saved"
    assert rakiura.checkpoint("ONB-002-D4/part-2") is False
    assert "revoked or has expired" in capsys.readouterr().out
    assert json.loads(creds.read_text())["keys"] == {}


def test_logout_forgets_the_saved_connection(creds):
    assert rakiura.login() is True
    rakiura.logout()
    assert json.loads(creds.read_text())["keys"] == {}


# ── run provenance ───────────────────────────────────────────────────────────


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    repo = tmp_path / "example-notebooks"
    (repo / "tutorials").mkdir(parents=True)
    (repo / "tutorials" / "tutorial_1.ipynb").write_text("{}")
    (repo / "README.md").write_text("hi")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@example.test", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "init")
    # A remote with credentials in it: they must never leave the computer.
    _git(repo, "remote", "add", "origin", "https://user:s3cret-in-url@github.com/someone/example-notebooks.git")
    monkeypatch.chdir(repo / "tutorials")
    return repo


def test_start_reports_repo_commit_and_relative_path_only(checkout, capsys):
    assert rakiura.start(notebook="tutorial_1.ipynb") is True
    body = posts("runs")[-1][2]
    assert set(body) <= RUN_FIELDS
    assert body["notebookPath"] == "tutorials/tutorial_1.ipynb"
    assert body["repo"] == "someone/example-notebooks"
    assert len(body["commitSha"]) == 40 and body["dirty"] is False
    assert body["environment"] == "local"
    wire = json.dumps([r[2] for r in Fake.received])
    assert "s3cret-in-url" not in wire and str(checkout) not in wire
    out = capsys.readouterr().out
    assert "running your fork at" in out and "ONB-002-D4" in out


def test_repo_relative_notebook_path_from_a_subfolder_is_not_doubled(checkout):
    # The kernel runs in tutorials/ (where the data folder is), and the
    # notebook names itself by its path inside the repository.
    rakiura.start(notebook="tutorials/tutorial_1.ipynb")
    body = posts("runs")[-1][2]
    assert body["notebookPath"] == "tutorials/tutorial_1.ipynb"
    assert body["dirty"] is False


def test_running_the_notebook_itself_is_not_edited_but_other_files_are(checkout):
    (checkout / "tutorials" / "tutorial_1.ipynb").write_text('{"cells": []}')
    rakiura.start(notebook="tutorial_1.ipynb")
    assert posts("runs")[-1][2]["dirty"] is False
    (checkout / "README.md").write_text("changed")
    rakiura.start(notebook="tutorial_1.ipynb")
    assert posts("runs")[-1][2]["dirty"] is True


def test_same_session_reuses_the_run(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    rakiura.start(notebook="tutorial_1.ipynb")
    assert len(posts("runs")) == 1


def test_absolute_path_outside_a_repository_sends_the_file_name_only(tmp_path):
    rakiura.start(notebook=str(tmp_path / "private" / "my_copy.ipynb"))
    body = posts("runs")[-1][2]
    assert body["notebookPath"] == "my_copy.ipynb"
    assert "repo" not in body and "commitSha" not in body


def test_colab_without_checkout_uses_the_named_repo(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.colab", types.ModuleType("google.colab"))
    rakiura.login(DEVICE_KEY)
    rakiura.start(notebook="tutorials/tutorial_1.ipynb", repo="example-org/example-notebooks")
    body = posts("runs")[-1][2]
    assert body["environment"] == "colab"
    assert body["repo"] == "example-org/example-notebooks" and "commitSha" not in body


def test_binder_reads_its_own_ref(monkeypatch):
    sha = "8f7378c" + "0" * 33
    monkeypatch.setenv("BINDER_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("BINDER_REF_URL", f"https://github.com/example-org/example-notebooks/tree/{sha}")
    rakiura.login(DEVICE_KEY)
    rakiura.start(notebook="tutorials/tutorial_1.ipynb")
    body = posts("runs")[-1][2]
    assert body["environment"] == "binder" and body["commitSha"] == sha


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@github.com:Owner/Repo.git", "Owner/Repo"),
        ("https://github.com/Owner/Repo", "Owner/Repo"),
        ("https://user:token@github.com/Owner/Repo.git", "Owner/Repo"),
        ("ssh://git@gitlab.com/group/repo.git", "gitlab.com/group/repo"),
        ("/local/path/repo", None),
        ("", None),
    ],
)
def test_remote_normalisation(remote, expected):
    assert rakiura._normalise_remote(remote) == expected


# ── done and submit ──────────────────────────────────────────────────────────


class _Fig:
    axes = [object()]

    def savefig(self, buf, format="png", **kw):
        buf.write(b"\x89PNG\r\n\x1a\n" + b"x" * 100)


def test_done_ticks_by_part_with_numbers_and_figures(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", figures=[_Fig()], numbers={"peak_ppm": 7.6, "converged": True}) is True
    tick = posts("checkpoint")[-1][2]
    assert tick == {"runId": "run-1", "checkpoint": "part-2", "numbers": {"peak_ppm": 7.6, "converged": True}}
    fig = posts("artifacts")[-1][2]
    assert fig["runId"] == "run-1" and fig["checkpoint"] == "part-2" and fig["mimeType"] == "image/png"
    assert "deliverableRef" not in fig
    assert "1 of 3 done" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [{"table": [1, 2, 3]}, {"x": float("nan")}, {"x": {"nested": 1}}, {}])
def test_done_refuses_anything_but_scalar_numbers(checkout, bad):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", numbers=bad) is False
    assert not posts("checkpoint")


def test_done_without_a_run_asks_for_the_notebook(capsys):
    rakiura.login(DEVICE_KEY)
    assert rakiura.done("part-2") is False
    assert "rakiura.start(notebook=" in capsys.readouterr().out


def test_unmapped_part_says_so_once(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    capsys.readouterr()
    Fake.checkpoint_mode = "unmapped"
    assert rakiura.done("part-9") is False
    assert rakiura.done("part-9") is False
    assert capsys.readouterr().out.count("isn't linked to a step yet") == 1


def test_submit_is_explicit_and_names_the_last_part(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    rakiura.done("part-2")
    assert not posts("submit")  # done() never submits on its own
    assert rakiura.submit(note="Ready") is True
    body = posts("submit")[-1][2]
    assert body["runId"] == "run-1" and body["checkpoint"] == "part-2" and body["note"] == "Ready"
    assert "notebookUrl" not in body  # the commit is not pushed anywhere
    assert "sent ONB-002-D4 for review with 2 items" in capsys.readouterr().out


def test_done_with_submit_true_submits(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", submit=True) is True
    assert posts("submit")[-1][2]["checkpoint"] == "part-2"


def test_submit_by_step_id(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    rakiura.submit(step="ONB-002-D6")
    body = posts("submit")[-1][2]
    assert body["deliverableRef"] == "ONB-002-D6" and body["runId"] == "run-1"


def test_share_notebook_needs_a_pushed_commit(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.share_notebook() is None
    assert "push" in capsys.readouterr().out
