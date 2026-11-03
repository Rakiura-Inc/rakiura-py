"""A run: which notebook, repository and commit this session is, and where it runs.

Only the fields in :func:`detect_run` leave the computer: no host or user
names, no absolute paths, no environment variables, no file contents.
"""

import platform
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._api import call
from ._config import base_url, expand_user
from ._environment import binder_ref, detect_notebook_path, environment
from ._git import git, is_inside, toplevel
from ._login import login
from ._session import RakiuraError, fail, say, session
from ._version import __version__

_SCP_REMOTE_RE = re.compile(r"^[\w.-]+@([\w.-]+):(.+)$")  # git@github.com:owner/name.git
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_OWNER_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")
# A repository path is at least owner/name, and host/group/subgroup at most.
MIN_REPO_SEGMENTS = 2
MAX_REPO_SEGMENTS = 3
SHORT_SHA_LENGTH = 7
# `git status --porcelain` lines are "XY path": two status columns and a space.
PORCELAIN_PATH_START = 3

WHERE_PHRASES = {"local": "on this computer", "colab": "in Colab", "binder": "on Binder"}
PIN_PHRASES = {"pinned": "the pinned commit", "fork_of_pin": "your fork at"}


def normalise_remote(remote: str | None) -> str | None:
    """``owner/name`` for GitHub, ``host/owner/name`` otherwise. Never credentials."""
    if not remote:
        return None
    remote = remote.strip()
    if match := _SCP_REMOTE_RE.match(remote):
        host, path = match.group(1), match.group(2)
    else:
        try:
            parts = urlsplit(remote)
        except ValueError:
            return None
        if not parts.hostname:
            return None
        host, path = parts.hostname, parts.path  # .hostname drops any user:token@
    path = path.strip("/").removesuffix(".git")
    segments = [s for s in path.split("/") if s]
    if len(segments) < MIN_REPO_SEGMENTS or not all(_PATH_SEGMENT_RE.match(s) for s in segments):
        return None
    repo = "/".join(segments[:MAX_REPO_SEGMENTS])
    return repo if host.lower() == "github.com" else f"{host.lower()}/{repo}"


def repo_relative(notebook: str | None, top: str | None) -> str | None:
    """The notebook path relative to its repository. Never an absolute path."""
    if not notebook:
        return None
    expanded = expand_user(notebook)
    path = Path(expanded)
    if top:
        # The documented form is the path inside the repository, so a relative
        # path that exists from the repository root is taken as-is, wherever
        # the kernel's working folder is; otherwise it is relative to here.
        relative_to = (Path(top) / path, Path.cwd() / path)
        candidates = [path] if path.is_absolute() else [p for p in relative_to if p.exists()]
        for full in candidates:
            if is_inside(full, top):
                return full.resolve().relative_to(Path(top).resolve()).as_posix()
    if path.is_absolute() or expanded.startswith(".."):
        return path.name  # outside any repository: the file name only
    return path.as_posix()


def _changed_files(top: str) -> list[str]:
    """Tracked files with uncommitted changes, relative to the repository root."""
    # Not stripped: the leading status column can be a space.
    status = git(["status", "--porcelain", "--untracked-files=no"], cwd=top, strip=False) or ""
    lines = [line for line in status.splitlines() if len(line) > PORCELAIN_PATH_START]
    return [line[PORCELAIN_PATH_START:].strip().strip('"') for line in lines]


def _checkout_info(top: str, notebook_path: str | None) -> dict[str, Any]:
    """repo, commitSha and dirty, read from the checkout at ``top``."""
    info: dict[str, Any] = {}
    sha = git(["rev-parse", "HEAD"], cwd=top)
    remote = normalise_remote(git(["remote", "get-url", "origin"], cwd=top))
    if sha and _SHA_RE.match(sha):
        info["commitSha"] = sha
        # A notebook that has been run is always modified: only other files count.
        info["dirty"] = any(f != notebook_path for f in _changed_files(top))
    if remote:
        info["repo"] = remote
    return info


def detect_run(notebook: str | None, repo: str | None) -> dict[str, Any]:
    """What a run reports, read by this helper itself. Only these fields leave the computer."""
    top = toplevel()
    info: dict[str, Any] = {
        "environment": environment(),
        "helperVersion": __version__,
        "pythonVersion": platform.python_version(),
    }
    path = repo_relative(notebook or detect_notebook_path(), top)
    if path and path.lower().endswith(".ipynb"):
        info["notebookPath"] = path
    if top:
        info.update(_checkout_info(top, path))
    else:
        binder_repo, binder_sha = binder_ref()
        if binder_repo and binder_sha:
            info["repo"], info["commitSha"], info["dirty"] = binder_repo, binder_sha, False
    if "repo" not in info and repo and _OWNER_NAME_RE.match(repo):
        info["repo"] = repo  # named by the notebook, no checkout around it
    return info


def _fingerprint(info: dict[str, Any]) -> tuple[Any, ...]:
    """What makes two start() calls the same run."""
    return (session.url, info.get("notebookPath"), info.get("repo"), info.get("commitSha"), info.get("dirty"))


def start(
    notebook: str | None = None,
    repo: str | None = None,
    url: str | None = None,
    quiet: bool = False,
) -> bool:
    """Open a run for this notebook session: connect if needed, then tell Rakiura
    which notebook, repository and commit this is, and where it runs.

    ``notebook`` is the notebook's path inside its repository, for example
    ``"onboarding/tutorial_1.ipynb"``. ``repo`` (``"owner/name"``) is only
    used when there is no git checkout around the notebook (Colab).
    Calling it again in the same session reuses the run.
    """
    session.quiet = quiet
    if url and base_url(url) != session.url:
        session.key = None
    if not session.key and not login(url=url, quiet=quiet):
        return False
    info = detect_run(notebook, repo)
    fingerprint = _fingerprint(info)
    if session.run_id and session.run_fingerprint == fingerprint:
        return True
    try:
        payload = call("POST", "/runs", info)
    except RakiuraError as exc:
        fail(str(exc))
        return False
    run = payload.get("run") or {}
    session.run_id = run.get("id")
    session.run_fingerprint = fingerprint
    session.run_info = info
    session.unmapped_said = set()
    lab = (payload.get("lab") or {}).get("name", "Rakiura")
    member = (payload.get("member") or {}).get("name", "you")
    say(f"rakiura: {member} in {lab}, {run_phrase(info, run.get('pinVerdict'))}.")
    _say_linked_steps(payload.get("parts") or [], info)
    return bool(session.run_id)


def _say_linked_steps(parts: list[Any], info: dict[str, Any]) -> None:
    """Which steps this notebook counts toward, or what to do when it counts toward none."""
    steps = sorted({p["step"]["displayId"] for p in parts if isinstance(p, dict) and p.get("step")})
    if steps:
        say(f"rakiura: this notebook counts toward {', '.join(steps)}.")
    elif info.get("notebookPath"):
        say("rakiura: this notebook isn't linked to a step yet, so tick items on the step page for now.")
    else:
        say('rakiura: say which notebook this is, for example rakiura.start(notebook="onboarding/tutorial_1.ipynb").')


def run_phrase(info: dict[str, Any], verdict: str | None) -> str:
    """Describe the run from what start() sent, e.g. "running your fork at 8f7378c (edited) in Colab"."""
    where = WHERE_PHRASES.get(info["environment"], "here")
    sha = info.get("commitSha")
    if not sha:
        return f"running {where}, no git checkout"
    what = PIN_PHRASES.get(verdict or "", "commit")
    edited = " (edited)" if info.get("dirty") else ""
    return f"running {what} {sha[:SHORT_SHA_LENGTH]}{edited} {where}"


def ensure_run() -> bool:
    """A run is open, opening one when the kernel can tell which notebook this is."""
    if session.run_id:
        return True
    if detect_notebook_path() is None:
        fail(
            'rakiura: run rakiura.start(notebook="path/to/this.ipynb") first, so Rakiura knows which notebook this is.'
        )
        return False
    return start(quiet=session.quiet)


def github_permalink(info: dict[str, Any] | None) -> str | None:
    """The notebook at its commit on GitHub, only when that commit is pushed and unedited."""
    if not info:
        return None
    repo, sha, path = info.get("repo"), info.get("commitSha"), info.get("notebookPath")
    if not (repo and sha and path) or repo.count("/") != 1 or info.get("dirty"):
        return None
    top = toplevel()
    if not top or not git(["branch", "-r", "--contains", sha], cwd=top):
        return None  # not pushed: the link would not open for anyone else
    return f"https://github.com/{repo}/blob/{sha}/{path}"


def share_notebook() -> str | None:
    """The link to this notebook at its commit on GitHub, once that commit is pushed."""
    link = github_permalink(session.run_info or detect_run(None, None))
    if link is None:
        fail("rakiura: commit and push your notebook first, so the link opens for your reviewer.")
        return None
    say(f"rakiura: {link}")
    return link
