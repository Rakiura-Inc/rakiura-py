"""Where the key is kept: one JSON file in the user's config folder, readable by them only.

The file maps each Rakiura address to the key that address issued, so a key is
only ever sent back to where it came from. It is never written inside a git
checkout, and never printed.
"""

import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._config import KEY_PREFIX, expand_user
from ._git import is_inside, toplevel
from ._session import say

FILE_FORMAT_VERSION = 1
OWNER_ONLY_FILE = 0o600
OWNER_ONLY_FOLDER = 0o700
GROUP_OR_OTHER_BITS = 0o077


def credentials_path() -> Path:
    """``RAKIURA_CREDENTIALS``, else the per-user config folder."""
    override = os.environ.get("RAKIURA_CREDENTIALS")
    if override:
        return Path(expand_user(override)).absolute()
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path(expand_user("~")) / ".config")
    return base / "rakiura" / "credentials"


def _read_keys() -> dict[str, Any]:
    """Every saved key, by address. Re-tightens the file if someone loosened its permissions."""
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if os.name == "posix":
        with contextlib.suppress(OSError):
            if path.stat().st_mode & GROUP_OR_OTHER_BITS:
                path.chmod(OWNER_ONLY_FILE)
    keys = data.get("keys") if isinstance(data, dict) else None
    return keys if isinstance(keys, dict) else {}


def _existing_ancestor(path: Path) -> Path | None:
    """``path`` itself, or the nearest folder above it that exists."""
    path = path.absolute()
    return next((folder for folder in (path, *path.parents) if folder.is_dir()), None)


def _in_git_checkout(path: Path) -> bool:
    """Is ``path`` inside a git working tree: the kernel's own, OR the one around
    the credentials folder itself (a dotfiles repository at ~ or ~/.config, or a
    ``RAKIURA_CREDENTIALS`` that points into a project)?"""
    tops = [toplevel()]
    anchor = _existing_ancestor(path.parent)
    if anchor:
        tops.append(toplevel(cwd=anchor))
    return any(top and is_inside(path, top) for top in tops)


def _write_keys(keys: dict[str, Any]) -> bool:
    """Write the file readable by you only (0600, folder 0700), atomically. Never inside a repository."""
    path = credentials_path()
    if _in_git_checkout(path):
        say("rakiura: not saving the connection inside a git repository. It lasts for this session only.")
        return False
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(mode=OWNER_ONLY_FOLDER, parents=True, exist_ok=True)
        if os.name == "posix" and not os.environ.get("RAKIURA_CREDENTIALS"):
            path.parent.chmod(OWNER_ONLY_FOLDER)  # our own folder may exist looser; never touch one you chose
        tmp.unlink(missing_ok=True)  # a leftover from a crash; O_EXCL below refuses to follow anything there
        # os.open, not Path.open: only it creates the file with 0600 from the start.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_ONLY_FILE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": FILE_FORMAT_VERSION, "keys": keys}, fh, indent=2)
        tmp.replace(path)
        if os.name == "posix":
            path.chmod(OWNER_ONLY_FILE)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


def _expired(expires_at: Any) -> bool:
    """True only for a readable ISO time that has passed."""
    if not isinstance(expires_at, str):
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except (ValueError, TypeError):  # unreadable, or a time without a zone: not known to have passed
        return False


def saved_key(url: str) -> str | None:
    """The unexpired key saved for ``url``, if any."""
    entry = _read_keys().get(url)
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX) or _expired(entry.get("expiresAt")):
        return None
    return key


def save_key(url: str, key: str, *, expires_at: Any, label: Any, lab: Any) -> bool:
    """Remember ``key`` for ``url`` on this computer. False when it could not be saved."""
    keys = _read_keys()
    keys[url] = {"key": key, "expiresAt": expires_at, "label": label, "lab": lab}
    return _write_keys(keys)


def forget_saved_key(url: str) -> None:
    keys = _read_keys()
    if url in keys:
        del keys[url]
        _write_keys(keys)


def colab_secret() -> str | None:
    """RAKIURA_KEY from Colab's Secrets panel, when this is Colab and it is set."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # only exists inside Colab

        value = userdata.get("RAKIURA_KEY")
    except Exception:  # noqa: BLE001 - a missing or refused secret just means "no key from Colab"
        return None
    return value if isinstance(value, str) else None
