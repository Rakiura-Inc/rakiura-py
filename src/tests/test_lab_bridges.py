"""Contract: the private names the lab helper packages call, called the way they call them.

nmrtrain (Rakiura-Inc/morning-star-nmr, ``nmrtrain/portal.py``) and habtrain
(Rakiura-Inc/limits-of-habitability, ``habtrain/portal.py``) decide whether a
notebook can connect without a click by calling ``rakiura._colab_secret()`` and
``rakiura._credentials_path()`` inside a try, and mute the helper through
``rakiura._session.quiet``. A refactor that drops or changes these fails
silently in their notebooks, so it must fail here. Keep until rakiura 0.4.0
offers ``key_available()`` and both bridges use it.
"""

import inspect
import os
import sys
import types
from pathlib import Path

import pytest

import rakiura
from rakiura import _credentials

FAKE_KEY = "rknb_" + "c" * 64


def nmrtrain_key_without_click(helper, default: Path) -> bool:
    """nmrtrain/portal.py ``_key_without_click``, with its ``quietly(call, None)`` written out."""

    def quietly(call):
        try:
            return call()
        except Exception:  # noqa: BLE001 - the bridge swallows everything, which is why this test exists
            return None

    if os.environ.get("RAKIURA_KEY") or quietly(lambda: helper._colab_secret()):
        return True
    path = quietly(lambda: helper._credentials_path()) or default.expanduser()
    return Path(path).exists()


def habtrain_key_without_click(helper, default: str) -> bool:
    """habtrain/portal.py ``_key_without_click``."""
    if os.environ.get("RAKIURA_KEY"):
        return True
    try:
        if helper._colab_secret():
            return True
    except Exception:  # noqa: BLE001, S110 - as the bridge does
        pass
    try:
        path = helper._credentials_path()
    except Exception:  # noqa: BLE001 - as the bridge does
        path = default
    return os.path.exists(path)  # noqa: PTH110 - as the bridge does


DEFAULT = ".config/rakiura/credentials"  # each bridge's own fallback, under ~
BRIDGES = [
    pytest.param(lambda home: nmrtrain_key_without_click(rakiura, home / DEFAULT), id="nmrtrain"),
    pytest.param(lambda home: habtrain_key_without_click(rakiura, str(home / DEFAULT)), id="habtrain"),
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home folder, so the bridges' own ~/.config fallback finds nothing."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


def in_colab(monkeypatch, get) -> None:
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.colab", types.SimpleNamespace(userdata=types.SimpleNamespace(get=get)))


def os_named(monkeypatch, name: str) -> None:
    """``os.name`` as ``name`` for rakiura's credentials module only; pathlib stays as it is."""
    monkeypatch.setattr(_credentials, "os", types.SimpleNamespace(name=name, environ=os.environ))


def saved_key_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "keys": {}}')
    return path


# ── the names exist with the 0.2.0 signatures ────────────────────────────────


@pytest.mark.parametrize("name", ["_colab_secret", "_credentials_path"])
def test_name_exists_and_takes_no_arguments(name):
    func = getattr(rakiura, name)
    assert callable(func)
    assert not inspect.signature(func).parameters


# ── _colab_secret ────────────────────────────────────────────────────────────


def test_colab_secret_returns_the_secret_in_colab(monkeypatch):
    in_colab(monkeypatch, lambda name: FAKE_KEY if name == "RAKIURA_KEY" else None)
    assert rakiura._colab_secret() == FAKE_KEY


def test_colab_secret_is_none_outside_colab(monkeypatch):
    monkeypatch.delitem(sys.modules, "google.colab", raising=False)
    assert rakiura._colab_secret() is None


def test_colab_secret_is_none_when_userdata_refuses(monkeypatch):
    def refuse(name):
        raise PermissionError(name)

    in_colab(monkeypatch, refuse)
    assert rakiura._colab_secret() is None


def test_colab_secret_is_none_for_a_non_string(monkeypatch):
    in_colab(monkeypatch, lambda name: 42)
    assert rakiura._colab_secret() is None


# ── _credentials_path: a str, the same as 0.2.0 computed ─────────────────────


def test_credentials_path_override(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere" / "creds"
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(target))
    assert rakiura._credentials_path() == str(target)


def test_credentials_path_override_with_tilde(monkeypatch, home):
    monkeypatch.setenv("RAKIURA_CREDENTIALS", "~/keys/rakiura")
    assert rakiura._credentials_path() == str(home / "keys" / "rakiura")


def test_credentials_path_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert rakiura._credentials_path() == str(tmp_path / "xdg" / "rakiura" / "credentials")


def test_credentials_path_appdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    os_named(monkeypatch, "nt")
    assert rakiura._credentials_path() == str(tmp_path / "appdata" / "rakiura" / "credentials")


def test_credentials_path_appdata_ignored_off_windows(monkeypatch, home, tmp_path):
    os_named(monkeypatch, "posix")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert rakiura._credentials_path() == str(home / ".config" / "rakiura" / "credentials")


def test_credentials_path_default(home):
    path = rakiura._credentials_path()
    assert isinstance(path, str)
    assert path == str(home / ".config" / "rakiura" / "credentials")


# ── each bridge, the way it calls them ───────────────────────────────────────


@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_finds_a_colab_secret(bridge, monkeypatch, home):
    in_colab(monkeypatch, lambda name: FAKE_KEY if name == "RAKIURA_KEY" else None)
    assert bridge(home) is True


@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_finds_a_key_saved_under_rakiura_credentials(bridge, monkeypatch, home, tmp_path):
    monkeypatch.setenv("RAKIURA_CREDENTIALS", str(saved_key_file(tmp_path / "custom" / "creds")))
    assert bridge(home) is True


@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_finds_a_key_saved_under_xdg_config_home(bridge, monkeypatch, home, tmp_path):
    saved_key_file(tmp_path / "xdg" / "rakiura" / "credentials")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert bridge(home) is True


@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_finds_a_key_saved_under_appdata(bridge, monkeypatch, home, tmp_path):
    saved_key_file(tmp_path / "appdata" / "rakiura" / "credentials")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    os_named(monkeypatch, "nt")
    assert bridge(home) is True


@pytest.mark.parametrize("bridge", BRIDGES)
def test_bridge_finds_no_key_when_there_is_none(bridge, monkeypatch, home):
    monkeypatch.delitem(sys.modules, "google.colab", raising=False)
    assert bridge(home) is False


# ── _session.quiet, and the public calls the bridges make ────────────────────


def test_session_quiet_mutes_and_unmutes_the_helper(capsys):
    say = sys.modules["rakiura._session"].say
    rakiura._session.quiet = True  # the bridges' _member_name
    say("muted")
    rakiura._session.quiet = False  # the bridges' _speak
    say("loud")
    assert capsys.readouterr().out == "loud\n"


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        ("start", (), {"notebook": "n.ipynb", "repo": "owner/name", "quiet": True}),
        ("me", (), {"show": False}),
        ("done", ("part-1",), {"figures": None, "numbers": None}),
        ("stage_text", ("text", "title"), {"step": "ONB-001-D1", "mime": "text/plain"}),
        ("submit", (), {"note": "done"}),
    ],
)
def test_public_calls_take_the_bridges_arguments(name, args, kwargs):
    inspect.signature(getattr(rakiura, name)).bind(*args, **kwargs)
