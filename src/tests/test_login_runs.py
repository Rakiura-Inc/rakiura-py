"""The 0.2 interface: one-click device login, the saved connection, run provenance, done() and submit(),
against a fake /api/notebook/v1 on localhost.
"""

import json
import os
import stat
import subprocess
import sys
import types
from typing import ClassVar

import pytest

import rakiura
from rakiura._config import base_url
from rakiura._runs import normalise_remote

from .conftest import FakeApi, Request, serve

DEVICE_KEY = "rknb_" + "d" * 64
DEVICE_CODE = "rknd_" + "e" * 64

# Exactly what a run may report. Anything else leaving the computer is a bug.
RUN_FIELDS = {"notebookPath", "repo", "commitSha", "dirty", "environment", "helperVersion", "pythonVersion"}

STEP = {"id": "d4", "displayId": "ONB-002-D4", "name": "Notebook 1", "url": "u"}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


class Fake(FakeApi):
    received: ClassVar[list[Request]] = []
    # Device login: how many polls answer "pending" before the outcome.
    pending_polls = 1
    outcome = "approve"  # approve | deny | expire | slow
    checkpoint_mode = "ok"  # ok | unmapped
    token_issued = 0

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        self.record()
        self.reply(404, _error("not_found", "no"))

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        body = self.read_json()
        self.record(body)
        if self.path == "/api/notebook/v1/device":
            self._device_start()
        elif self.path == "/api/notebook/v1/device/token":
            self._device_token(body)
        elif self.headers.get("Authorization") != f"Bearer {DEVICE_KEY}":
            self.reply(401, _error("unauthenticated", "This key was not accepted."))
        else:
            self._keyed(body)

    def _device_start(self) -> None:
        verification = f"http://{self.headers['Host']}/oauth/device"  # this fake, as the client reached it
        self.reply(
            200,
            {
                "ok": True,
                "deviceCode": DEVICE_CODE,
                "userCode": "WXYZ-BCDF",
                "verificationUri": verification,
                "verificationUriComplete": f"{verification}?code=WXYZ-BCDF",
                "expiresIn": 600,
                "interval": 1,
            },
        )

    def _device_token(self, body: dict) -> None:
        assert body == {"deviceCode": DEVICE_CODE}
        assert "Authorization" not in self.headers
        polls = sum(1 for r in Fake.received if r[1] == "/api/notebook/v1/device/token")
        if Fake.outcome == "slow" and polls == 1:
            self.reply(400, _error("slow_down", "slow"))
        elif polls <= Fake.pending_polls:
            self.reply(400, _error("authorization_pending", "wait"))
        elif Fake.outcome == "deny":
            self.reply(400, _error("access_denied", "The sign-in was cancelled in Rakiura."))
        elif Fake.outcome == "expire":
            message = "The sign-in code expired. Run rakiura.login() again for a new one."
            self.reply(400, _error("expired_token", message))
        elif Fake.token_issued:
            self.reply(400, _error("invalid_grant", "That sign-in was already used."))
        else:
            Fake.token_issued += 1
            self.reply(
                200,
                {
                    "ok": True,
                    "key": DEVICE_KEY,
                    "expiresAt": "2099-01-01T00:00:00.000Z",
                    "label": "Connected from your computer, Oct 6",
                    "lab": {"id": "lab-1", "name": "Example Lab"},
                    "member": {"name": "Test Member"},
                },
            )

    def _keyed(self, body: dict) -> None:
        if self.path == "/api/notebook/v1/runs":
            self.reply(
                200,
                {
                    "ok": True,
                    "run": {"id": "run-1", "pinVerdict": "fork_of_pin"},
                    "lab": {"id": "lab-1", "name": "Example Lab"},
                    "member": {"name": "Test Member"},
                    "notebook": {"path": body.get("notebookPath"), "title": "Tutorial 1"},
                    "parts": [{"part": "part-2", "step": STEP}],
                },
            )
        elif self.path == "/api/notebook/v1/checkpoint" and Fake.checkpoint_mode == "unmapped":
            message = '"part-9" in Tutorial 1 isn\'t linked to a step yet, so nothing was logged.'
            self.reply(404, _error("unmapped", message))
        elif self.path == "/api/notebook/v1/checkpoint":
            self.reply(
                200,
                {
                    "ok": True,
                    "deliverable": STEP,
                    "item": {"key": body.get("checkpoint"), "label": "Part 2", "tag": "ONB-002-D4/part-2"},
                    "checked": True,
                    "changed": True,
                    "progress": {"done": 1, "total": 3},
                },
            )
        elif self.path == "/api/notebook/v1/artifacts":
            self.reply(
                200,
                {
                    "ok": True,
                    "artifact": {"id": "a1", "title": body.get("title"), "kind": "file"},
                    "deliverable": STEP,
                    "created": True,
                },
            )
        elif self.path == "/api/notebook/v1/submit":
            self.reply(
                200,
                {
                    "ok": True,
                    "submission": {"id": "s1", "version": 1},
                    "deliverable": {**STEP, "url": "https://labs.example.test/s"},
                    "attached": 2,
                },
            )
        else:
            self.reply(404, _error("not_found", "no"))


@pytest.fixture(scope="module")
def server():
    yield from serve(Fake)


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
    monkeypatch.setenv("RAKIURA_URL", server)
    monkeypatch.setattr(rakiura._login.time, "sleep", lambda _s: None)
    # Outside any git repository unless a test makes one.
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)


def posts(path):
    return [r for r in Fake.received if r[0] == "POST" and r[1] == f"/api/notebook/v1/{path}"]


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── device login ─────────────────────────────────────────────────────────────


def test_device_login_saves_key_0600_outside_repo_and_never_prints_it(capsys, creds):
    assert rakiura.login() is True
    out = capsys.readouterr().out
    assert "WXYZ-BCDF" in out
    assert "oauth/device?code=WXYZ-BCDF" in out
    assert "connected as Test Member in Example Lab. This computer remembers it for 30 days." in out
    assert DEVICE_KEY not in out
    assert DEVICE_CODE not in out
    assert rakiura._session.key == DEVICE_KEY
    saved = json.loads(creds.read_text())
    assert saved["keys"][rakiura._session.url] == {
        "key": DEVICE_KEY,
        "expiresAt": "2099-01-01T00:00:00.000Z",
        "label": "Connected from your computer, Oct 6",
        "lab": "Example Lab",
    }
    if os.name == "posix":
        assert _mode(creds) == 0o600
        assert _mode(creds.parent) == 0o700


def test_saved_key_is_reused_without_a_new_sign_in():
    assert rakiura.login() is True
    rakiura.logout(forget=False)
    Fake.received.clear()
    assert rakiura.login() is True
    assert Fake.received == []  # no device login: the saved connection
    assert rakiura._session.key_source == "saved"


def test_saved_key_is_only_sent_to_the_address_it_came_from(creds):
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"version": 1, "keys": {"https://elsewhere.example": {"key": DEVICE_KEY}}}))
    assert rakiura.login() is True
    assert posts("device"), "a key saved for another address must not be used here"


def test_an_expired_saved_key_is_not_used(creds):
    creds.parent.mkdir(parents=True)
    entry = {"key": DEVICE_KEY, "expiresAt": "2001-01-01T00:00:00.000Z"}
    creds.write_text(json.dumps({"version": 1, "keys": {base_url(None): entry}}))
    assert rakiura.login() is True
    assert rakiura._session.key_source == "device"


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
    assert rakiura._session.key is None
    assert not creds.exists()


def test_expired_sign_in_returns_false(capsys):
    Fake.outcome = "expire"
    assert rakiura.login() is False
    assert "expired" in capsys.readouterr().out


def test_slow_down_backs_off(monkeypatch):
    waits = []
    monkeypatch.setattr(rakiura._login.time, "sleep", waits.append)
    Fake.outcome = "slow"
    assert rakiura.login() is True
    assert waits[0] == 1
    assert max(waits) >= 6


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
    folder.chmod(0o755)  # deliberately loose: the helper must tighten it
    monkeypatch.delenv("RAKIURA_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    assert rakiura.login() is True
    assert _mode(folder) == 0o700
    assert _mode(folder / "credentials") == 0o600
    assert not [p for p in folder.iterdir() if ".tmp-" in p.name]


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_a_loosened_credentials_file_is_tightened_on_read(creds):
    assert rakiura.login() is True
    creds.chmod(0o644)  # deliberately loose: the helper must tighten it
    rakiura.logout(forget=False)
    assert rakiura.login() is True
    assert _mode(creds) == 0o600


def test_env_key_wins_and_is_never_saved(monkeypatch, creds):
    monkeypatch.setenv("RAKIURA_KEY", DEVICE_KEY)
    assert rakiura.login() is True
    assert rakiura._session.key_source == "env"
    assert not posts("device")
    assert not creds.exists()


def test_colab_secret_is_read(monkeypatch):
    userdata = types.SimpleNamespace(get=lambda name: DEVICE_KEY if name == "RAKIURA_KEY" else None)
    colab = types.SimpleNamespace(userdata=userdata)  # all the helper reads from google.colab
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    assert rakiura.login() is True
    assert rakiura._session.key_source == "colab"


def test_revoked_saved_key_is_forgotten_on_this_computer(creds, capsys):
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"version": 1, "keys": {base_url(None): {"key": "rknb_" + "f" * 64}}}))
    assert rakiura.login() is True
    assert rakiura._session.key_source == "saved"
    assert rakiura.checkpoint("ONB-002-D4/part-2") is False
    assert "revoked or has expired" in capsys.readouterr().out
    assert json.loads(creds.read_text())["keys"] == {}


def test_logout_forgets_the_saved_connection(creds):
    assert rakiura.login() is True
    rakiura.logout()
    assert json.loads(creds.read_text())["keys"] == {}


# ── run provenance ───────────────────────────────────────────────────────────


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


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
    assert len(body["commitSha"]) == 40
    assert body["dirty"] is False
    assert body["environment"] == "local"
    assert body["helperVersion"] == rakiura.__version__
    wire = json.dumps([r[2] for r in Fake.received])
    assert "s3cret-in-url" not in wire
    assert str(checkout) not in wire
    out = capsys.readouterr().out
    assert f"running your fork at {body['commitSha'][:7]} on this computer" in out
    assert "this notebook counts toward ONB-002-D4." in out


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
    assert "repo" not in body
    assert "commitSha" not in body


def test_colab_without_checkout_uses_the_named_repo(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.colab", types.ModuleType("google.colab"))
    rakiura.login(DEVICE_KEY)
    rakiura.start(notebook="tutorials/tutorial_1.ipynb", repo="example-org/example-notebooks")
    body = posts("runs")[-1][2]
    assert body["environment"] == "colab"
    assert body["repo"] == "example-org/example-notebooks"
    assert "commitSha" not in body


def test_binder_reads_its_own_ref(monkeypatch):
    sha = "8f7378c" + "0" * 33
    monkeypatch.setenv("BINDER_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("BINDER_REF_URL", f"https://github.com/example-org/example-notebooks/tree/{sha}")
    rakiura.login(DEVICE_KEY)
    rakiura.start(notebook="tutorials/tutorial_1.ipynb")
    body = posts("runs")[-1][2]
    assert body["environment"] == "binder"
    assert body["commitSha"] == sha


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:Owner/Repo.git", "Owner/Repo"),
        ("https://github.com/Owner/Repo", "Owner/Repo"),
        ("https://user:token@github.com/Owner/Repo.git", "Owner/Repo"),
        ("ssh://git@gitlab.com/group/repo.git", "gitlab.com/group/repo"),
        ("https://gitlab.com/group/sub/repo/extra", "gitlab.com/group/sub/repo"),
        ("https://github.com/only-owner", None),
        ("https://github.com/Owner/bad name", None),
        ("/local/path/repo", None),
        ("", None),
        (None, None),
    ],
)
def test_remote_normalisation(remote, expected):
    assert normalise_remote(remote) == expected


# ── done and submit ──────────────────────────────────────────────────────────


class _Fig:
    axes: ClassVar[list[object]] = [object()]

    def savefig(self, buf, format="png", **_kw):  # noqa: A002 - matplotlib's keyword
        buf.write(b"\x89PNG\r\n\x1a\n" + b"x" * 100)


def test_done_ticks_by_part_with_numbers_and_figures(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", figures=[_Fig()], numbers={"peak_ppm": 7.6, "converged": True}) is True
    tick = posts("checkpoint")[-1][2]
    assert tick == {"runId": "run-1", "checkpoint": "part-2", "numbers": {"peak_ppm": 7.6, "converged": True}}
    fig = posts("artifacts")[-1][2]
    assert fig["runId"] == "run-1"
    assert fig["checkpoint"] == "part-2"
    assert fig["mimeType"] == "image/png"
    assert fig["title"] == "part-2 figure"
    assert "deliverableRef" not in fig
    assert 'ticked "Part 2" on ONB-002-D4. 1 of 3 done.' in capsys.readouterr().out


def test_done_names_several_figures_by_number(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", figures=[_Fig(), _Fig()]) is True
    assert [r[2]["title"] for r in posts("artifacts")] == ["part-2 figure 1", "part-2 figure 2"]


@pytest.mark.parametrize("bad", [{"table": [1, 2, 3]}, {"x": float("nan")}, {"x": {"nested": 1}}, {}, {1: 2}])
def test_done_refuses_anything_but_scalar_numbers(checkout, bad):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", numbers=bad) is False
    assert not posts("checkpoint")


@pytest.mark.parametrize("bad", ["", " ", "-starts-with-dash", "has space", "x" * 81, 7])
def test_done_refuses_a_bad_part_name(checkout, bad, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done(bad) is False
    assert not posts("checkpoint")
    assert "a part name is letters, digits" in capsys.readouterr().out


def test_done_refuses_something_that_is_not_a_figure(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", figures=[object()]) is False
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
    assert body == {"runId": "run-1", "checkpoint": "part-2", "note": "Ready"}  # no notebookUrl: nothing is pushed
    assert (
        "sent ONB-002-D4 for review with 2 items. See it here: https://labs.example.test/s" in capsys.readouterr().out
    )


def test_submit_with_nothing_done_asks_which_step(capsys):
    rakiura.login(DEVICE_KEY)
    assert rakiura.submit() is False
    assert not posts("submit")
    assert "say which step to submit" in capsys.readouterr().out


def test_done_with_submit_true_submits(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.done("part-2", submit=True) is True
    assert posts("submit")[-1][2]["checkpoint"] == "part-2"


def test_submit_by_step_id(checkout):
    rakiura.start(notebook="tutorial_1.ipynb")
    rakiura.submit(step="ONB-002-D6")
    body = posts("submit")[-1][2]
    assert body["deliverableRef"] == "ONB-002-D6"
    assert body["runId"] == "run-1"


def test_share_notebook_needs_a_pushed_commit(checkout, capsys):
    rakiura.start(notebook="tutorial_1.ipynb")
    assert rakiura.share_notebook() is None
    assert "push" in capsys.readouterr().out
