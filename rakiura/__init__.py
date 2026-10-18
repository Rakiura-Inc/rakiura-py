"""rakiura: log your progress on Rakiura Labs steps from a Jupyter notebook.

Four lines::

    import rakiura
    rakiura.start(notebook="onboarding/tutorial_1.ipynb")  # first time: open the link, click Allow
    rakiura.done("part-2")                                  # ticks that part on its step
    rakiura.done("exercise-1", figures=[fig])               # ticks it and adds the figure
    rakiura.submit()                                        # only when you choose: sends the step for review

Design rules:

* Connecting is one click. ``rakiura.login()`` (which ``start()`` calls)
  prints a link; open it while signed in to Rakiura, check the code matches,
  and click Allow. The key it receives is saved in your config directory
  (``~/.config/rakiura/credentials``, readable only by you), never in the
  notebook or its repository, and only ever sent back to the Rakiura address
  it came from. ``RAKIURA_KEY`` in the environment overrides it, and Colab's
  Secrets panel works too. A key never needs to be written in a notebook.
* Every tick and figure carries a run: the notebook, the repository and
  commit actually run, whether the checkout was edited, and where it ran
  (laptop, Colab, Binder). Forks and personal copies are labelled, never
  refused. Nothing else about your computer is sent: no host or user names,
  no absolute paths, no environment variables, no file contents.
* Which step a part counts toward is decided in Rakiura, so a notebook only
  carries part names like ``"part-2"``.
* Nothing is submitted for review unless you run ``rakiura.submit()``.
* Nothing here ever raises into the notebook. A missing key, a laptop that is
  offline, a revoked key or a server that says no all print ONE line and
  return ``None`` (or ``False``), so a tutorial keeps running.
* Pure standard library. ``requests`` is used when it is installed, otherwise
  ``urllib``; neither is required.
* The key only travels over https (plain http only to this computer, for
  testing), and never follows a redirect.

The cell-tag functions of 0.1 (``me``, ``checkpoint``, ``stage_figure`` ...)
still work.
"""

from __future__ import annotations

import base64
import functools
import html
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

__all__ = [
    "login",
    "logout",
    "start",
    "done",
    "submit",
    "share_notebook",
    "me",
    "checkpoint",
    "uncheck",
    "checkpoint_cell",
    "stage_figure",
    "stage_link",
    "stage_text",
    "RakiuraError",
]

__version__ = "0.2.0"

DEFAULT_URL = "https://labs.rakiura.co"
API_PREFIX = "/api/notebook/v1"
KEY_PREFIX = "rknb_"
FILE_TEXT_MAX_BYTES = 50 * 1024
MAX_NUMBERS = 20
_TIMEOUT_SECONDS = 15
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_SHA_RE = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")


def _url_allowed(url: str) -> bool:
    """https anywhere; plain http only to this computer."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS


class RakiuraError(Exception):
    """Raised ONLY by the private request helpers; public calls catch it."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class _Session:
    key: Optional[str] = None
    url: str = DEFAULT_URL
    quiet: bool = False
    # Where the key came from: argument | env | colab | saved | device | prompt
    key_source: Optional[str] = None
    # The current run (rakiura.start()), and what it was opened for.
    run_id: Optional[str] = None
    run_fingerprint: Optional[Tuple[Any, ...]] = None
    run_info: Optional[Dict[str, Any]] = None
    last_part: Optional[str] = None
    unmapped_said: set = set()
    # The last error, for people who want to look (never printed twice).
    last_error: Optional[str] = None


_session = _Session()


def _say(line: str) -> None:
    """One line, to stdout, unless the caller asked for silence."""
    if not _session.quiet:
        print(line)


def _fail(line: str):
    _session.last_error = line
    _say(line)
    return None


def _base_url(url: Optional[str]) -> Optional[str]:
    target = (url or os.environ.get("RAKIURA_URL") or DEFAULT_URL).rstrip("/")
    return target if _url_allowed(target) else None


# ── where the key is kept ────────────────────────────────────────────────────


def _credentials_path() -> str:
    """``RAKIURA_CREDENTIALS``, else the per-user config directory."""
    override = os.environ.get("RAKIURA_CREDENTIALS")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if os.name == "nt" and os.environ.get("APPDATA"):
        base = os.environ["APPDATA"]
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "rakiura", "credentials")


def _git(args: List[str], cwd: Optional[str] = None, strip: bool = True) -> Optional[str]:
    """One git command's stdout (stripped unless ``strip=False``), or None (no git, not a checkout, error)."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd or os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.decode("utf-8", "replace")
    return text.strip() if strip else text


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _read_credentials() -> Dict[str, Any]:
    path = _credentials_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if os.name == "posix":
        try:
            if os.stat(path).st_mode & 0o077:
                os.chmod(path, 0o600)  # someone loosened it: readable by you only again
        except OSError:
            pass
    keys = data.get("keys") if isinstance(data, dict) else None
    return keys if isinstance(keys, dict) else {}


def _existing_ancestor(path: str) -> Optional[str]:
    """``path`` itself, or the nearest folder above it that exists."""
    folder = os.path.abspath(path)
    while not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent
    return folder


def _in_git_checkout(path: str) -> bool:
    """Is ``path`` inside a git working tree: the kernel's own, OR the one around
    the credentials folder itself (a dotfiles repository at ~ or ~/.config, or a
    ``RAKIURA_CREDENTIALS`` that points into a project)?"""
    tops = [_git(["rev-parse", "--show-toplevel"])]
    anchor = _existing_ancestor(os.path.dirname(path))
    if anchor:
        tops.append(_git(["rev-parse", "--show-toplevel"], cwd=anchor))
    return any(top and _inside(path, top) for top in tops)


def _write_credentials(keys: Dict[str, Any]) -> bool:
    """Write the file readable by you only (0600, folder 0700), atomically. Never inside a repository."""
    path = _credentials_path()
    folder = os.path.dirname(path)
    if _in_git_checkout(path):
        _say("rakiura: not saving the connection inside a git repository. It lasts for this session only.")
        return False
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if os.name == "posix" and not os.environ.get("RAKIURA_CREDENTIALS"):
            os.chmod(folder, 0o700)  # our own folder may exist looser; never touch one you chose
        try:
            os.unlink(tmp)  # a leftover from a crash; O_EXCL below refuses to follow anything there
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "keys": keys}, fh, indent=2)
        os.replace(tmp, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _saved_key(url: str) -> Optional[str]:
    entry = _read_credentials().get(url)
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        return None
    expires = entry.get("expiresAt")
    if isinstance(expires, str):
        try:
            from datetime import datetime, timezone

            if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            pass
    return key


def _save_key(url: str, key: str, info: Dict[str, Any]) -> bool:
    keys = _read_credentials()
    keys[url] = {"key": key, **{k: v for k, v in info.items() if k in ("expiresAt", "label", "lab")}}
    return _write_credentials(keys)


def _forget_saved_key(url: str) -> None:
    keys = _read_credentials()
    if url in keys:
        del keys[url]
        _write_credentials(keys)


def _colab_secret() -> Optional[str]:
    """RAKIURA_KEY from Colab's Secrets panel, when this is Colab and it is set."""
    if "google.colab" not in sys.modules:
        return None
    try:
        from google.colab import userdata  # type: ignore

        value = userdata.get("RAKIURA_KEY")
        return value if isinstance(value, str) else None
    except Exception:
        return None


# ── login ────────────────────────────────────────────────────────────────────


def login(
    key: Optional[str] = None,
    url: Optional[str] = None,
    quiet: bool = False,
    remember: bool = True,
) -> bool:
    """Connect this notebook session to Rakiura.

    Order: the ``key`` argument (discouraged: a notebook gets shared), then
    ``RAKIURA_KEY`` from the environment, then Colab's Secrets panel, then the
    connection saved on this computer, then a one-click sign-in: a link to
    open while signed in to Rakiura, where you click Allow. The key it
    receives is saved in your config directory (``remember=False`` keeps it in
    memory only). Returns True when a key is set.
    """
    _session.quiet = quiet
    target = _base_url(url)
    if target is None:
        _session.key = None
        _fail("rakiura: the Rakiura address must start with https://. No key was set.")
        return False
    if target != _session.url:
        # A key belongs to the address it came from: never carry it to another
        # one, even if the sign-in below fails.
        _session.key = None
        _session.key_source = None
        _session.run_id = None
        _session.run_fingerprint = None
    _session.url = target

    if key:
        _say("rakiura: a key written in a notebook can leak when the notebook is shared. Run rakiura.login() with no key to connect with one click instead.")
        return _set_key(key, "argument")
    for source, candidate in (("env", os.environ.get("RAKIURA_KEY")), ("colab", _colab_secret())):
        if candidate:
            return _set_key(candidate, source)
    saved = _saved_key(target)
    if saved:
        _session.key = saved
        _session.key_source = "saved"
        return True
    return _device_login(target, remember)


def _set_key(candidate: str, source: str) -> bool:
    candidate = (candidate or "").strip()
    if not candidate.startswith(KEY_PREFIX):
        _session.key = None
        _fail("rakiura: that does not look like a notebook key (they start with rknb_). Run rakiura.login() with no key to connect with one click.")
        return False
    _session.key = candidate
    _session.key_source = source
    _say("rakiura: key set for this session.")
    return True


def logout(forget: bool = True) -> None:
    """Forget the key for this session and, with ``forget``, the one saved on this computer."""
    if forget and _session.key_source in ("saved", "device"):
        _forget_saved_key(_session.url)
    _session.key = None
    _session.key_source = None
    _session.run_id = None
    _session.run_fingerprint = None
    _session.run_info = None
    _session.last_part = None


def _environment() -> str:
    """local, colab or binder: read from what this kernel can see, nothing sent but the word."""
    if "google.colab" in sys.modules or os.environ.get("COLAB_RELEASE_TAG"):
        return "colab"
    if any(os.environ.get(v) for v in ("BINDER_SERVICE_HOST", "BINDER_REPO_URL", "BINDER_LAUNCH_HOST", "BINDER_REF_URL")):
        return "binder"
    return "local"


def _show_link(link: str, code: str) -> None:
    """The sign-in link as one printed line, clickable in Jupyter and Colab too."""
    _say(f"rakiura: open {link} and click Allow (check the code shows {code}).")
    if _session.quiet:
        return
    try:
        from IPython import get_ipython  # type: ignore
        from IPython.display import HTML, display  # type: ignore

        if get_ipython() is not None:
            safe_link, safe_code = html.escape(link, quote=True), html.escape(code)
            display(HTML(f'<a href="{safe_link}" target="_blank" rel="noopener">Connect this notebook to Rakiura</a> (code {safe_code})'))
    except Exception:
        pass
    if _environment() == "local" and not os.environ.get("RAKIURA_NO_BROWSER"):
        try:
            import webbrowser

            webbrowser.open(link, new=2)
        except Exception:
            pass


def _device_login(url: str, remember: bool) -> bool:
    try:
        started = _post_public(url, "/device", {"environment": _environment(), "helperVersion": __version__})
    except RakiuraError as exc:
        if "HTTP 404" in str(exc):
            return _prompt_for_key()
        _fail(str(exc))
        return False
    device_code = started.get("deviceCode")
    link = started.get("verificationUriComplete") or started.get("verificationUri")
    code = started.get("userCode", "")
    if not isinstance(device_code, str) or not isinstance(link, str) or not _url_allowed(link):
        _fail(f"rakiura: unexpected reply from {url}.")
        return False
    _show_link(link, code)
    interval = max(1, int(started.get("interval") or 5))
    deadline = time.monotonic() + int(started.get("expiresIn") or 600)
    try:
        while time.monotonic() < deadline:
            time.sleep(interval)
            status, payload = _post_public_raw(url, "/device/token", {"deviceCode": device_code})
            if status == 200 and payload.get("ok") and isinstance(payload.get("key"), str):
                key = payload["key"]
                if not key.startswith(KEY_PREFIX):
                    break
                _session.key = key
                _session.key_source = "device"
                saved = remember and _save_key(url, key, {
                    "expiresAt": payload.get("expiresAt"),
                    "label": payload.get("label"),
                    "lab": (payload.get("lab") or {}).get("name"),
                })
                lab = (payload.get("lab") or {}).get("name", "Rakiura")
                member = (payload.get("member") or {}).get("name", "you")
                where = " This computer remembers it for 30 days." if saved else ""
                _say(f"rakiura: connected as {member} in {lab}.{where}")
                return True
            err = payload.get("error") if isinstance(payload, dict) else None
            code_ = err.get("code") if isinstance(err, dict) else None
            if code_ == "authorization_pending":
                continue
            if code_ == "slow_down":
                interval += 5
                continue
            message = (err.get("message") if isinstance(err, dict) else None) or f"HTTP {status}"
            _fail(f"rakiura: {message}")
            return False
    except KeyboardInterrupt:
        _fail("rakiura: sign-in stopped. Run rakiura.login() to try again.")
        return False
    except RakiuraError as exc:
        _fail(str(exc))
        return False
    _fail("rakiura: the sign-in code expired. Run rakiura.login() again for a new one.")
    return False


def _prompt_for_key() -> bool:
    """For a Rakiura that has no one-click sign-in: ask for a key, hidden."""
    try:
        import getpass

        candidate = getpass.getpass("Rakiura notebook key (from Settings, Notebook key): ")
    except Exception:  # no terminal, no prompt
        candidate = None
    if not (candidate or "").strip():
        _fail("rakiura: no key given. Set RAKIURA_KEY, or create one in Rakiura under Settings, Notebook key.")
        return False
    return _set_key(candidate or "", "prompt")


# ── transport ────────────────────────────────────────────────────────────────


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not _session.key:
        if not login(quiet=_session.quiet):
            raise RakiuraError("rakiura: not connected.")
    url = f"{_session.url}{API_PREFIX}{path}"
    headers = {
        "Authorization": f"Bearer {_session.key}",
        "Accept": "application/json",
        "User-Agent": f"rakiura-py/{__version__}",
    }
    status, payload = _exchange(method, url, headers, body)
    if status >= 400 or not payload.get("ok"):
        err = payload.get("error")
        message = (err.get("message") if isinstance(err, dict) else None) or f"HTTP {status}"
        if status == 401:
            # A revoked or expired key: forget it here and on this computer.
            if _session.key_source in ("saved", "device"):
                _forget_saved_key(_session.url)
                message = "this connection was revoked or has expired. Run rakiura.login() to connect again."
            _session.key = None
            _session.key_source = None
        code = err.get("code") if isinstance(err, dict) else None
        raise RakiuraError(f"rakiura: {message}", code)
    return payload


def _exchange(method: str, url: str, headers: Dict[str, str], body: Optional[Dict[str, Any]]):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    try:
        status, text = _send(method, url, headers, data)
    except Exception as exc:  # offline, DNS, refused, timeout: one line, no traceback
        raise RakiuraError(f"rakiura: could not reach {_session.url} ({exc.__class__.__name__}). Your work is fine; tick the box on the step page instead.")
    if 300 <= status < 400:
        raise RakiuraError(f"rakiura: {_session.url} answered with a redirect, so nothing was sent on. Check the address you gave rakiura.login().")
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        raise RakiuraError(f"rakiura: unexpected reply from {_session.url} (HTTP {status}).")
    if not isinstance(payload, dict):
        raise RakiuraError(f"rakiura: unexpected reply from {_session.url} (HTTP {status}).")
    return status, payload


def _post_public_raw(url: str, path: str, body: Dict[str, Any]):
    """A POST with no key (the sign-in endpoints)."""
    headers = {"Accept": "application/json", "User-Agent": f"rakiura-py/{__version__}"}
    return _exchange("POST", f"{url}{API_PREFIX}{path}", headers, body)


def _post_public(url: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    status, payload = _post_public_raw(url, path, body)
    if status >= 400 or not payload.get("ok"):
        err = payload.get("error")
        message = (err.get("message") if isinstance(err, dict) else None) or f"HTTP {status}"
        raise RakiuraError(f"rakiura: {message}" if status != 404 else f"rakiura: HTTP 404 ({message})")
    return payload


def _send(method: str, url: str, headers: Dict[str, str], data: Optional[bytes]):
    """One request, redirects NOT followed (a 3xx comes back as its status)."""
    try:
        import requests  # type: ignore
    except ImportError:
        requests = None  # type: ignore
    if requests is not None:
        # An explicit (no-op) auth stops requests from replacing the key's
        # Authorization header with a ~/.netrc entry; proxies still apply.
        resp = requests.request(
            method,
            url,
            headers=headers,
            data=data,
            timeout=_TIMEOUT_SECONDS,
            allow_redirects=False,
            auth=lambda r: r,
        )
        return resp.status_code, resp.text
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # noqa: D401
            return None  # urllib then raises HTTPError with the 3xx status

    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=_TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as http_err:
        return http_err.code, http_err.read().decode("utf-8", "replace")


def _error_code(exc: RakiuraError) -> Optional[str]:
    return getattr(exc, "code", None)


# ── run provenance ───────────────────────────────────────────────────────────


def _normalise_remote(remote: Optional[str]) -> Optional[str]:
    """``owner/name`` for GitHub, ``host/owner/name`` otherwise. Never credentials."""
    if not remote:
        return None
    remote = remote.strip()
    m = re.match(r"^[\w.-]+@([\w.-]+):(.+)$", remote)  # git@github.com:owner/name.git
    if m:
        host, path = m.group(1), m.group(2)
    else:
        try:
            parts = urlsplit(remote)
        except ValueError:
            return None
        if not parts.hostname:
            return None
        host, path = parts.hostname, parts.path  # .hostname drops any user:token@
    path = re.sub(r"\.git$", "", path.strip("/"))
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2 or not all(re.match(r"^[A-Za-z0-9_.-]+$", s) for s in segments):
        return None
    repo = "/".join(segments[:3])
    return repo if host.lower() == "github.com" else f"{host.lower()}/{repo}"


def _binder_ref() -> Tuple[Optional[str], Optional[str]]:
    """(repo, sha) from Binder's own variables, when this is Binder without a .git folder."""
    ref = os.environ.get("BINDER_REF_URL") or ""
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/tree/([0-9a-f]{40})", ref)
    return (m.group(1), m.group(2)) if m else (None, None)


def _detect_notebook_path() -> Optional[str]:
    """The running notebook's path, when Jupyter or VS Code says so."""
    candidate = os.environ.get("JPY_SESSION_NAME")
    try:
        from IPython import get_ipython  # type: ignore

        shell = get_ipython()
        if shell is not None:
            candidate = shell.user_ns.get("__vsc_ipynb_file__") or candidate
    except Exception:
        pass
    return candidate if candidate and candidate.endswith(".ipynb") else None


def _repo_relative(notebook: Optional[str], top: Optional[str]) -> Optional[str]:
    """The notebook path relative to its repository. Never an absolute path."""
    if not notebook:
        return None
    path = os.path.expanduser(notebook)
    if top:
        # The documented form is the path inside the repository, so a relative
        # path that exists from the repository root is taken as-is, wherever
        # the kernel's working folder is; otherwise it is relative to here.
        if os.path.isabs(path):
            candidates = [path]
        else:
            candidates = [p for p in (os.path.join(top, path), os.path.join(os.getcwd(), path)) if os.path.exists(p)]
        for full in candidates:
            if _inside(full, top):
                return os.path.relpath(os.path.realpath(full), os.path.realpath(top)).replace(os.sep, "/")
    if os.path.isabs(path) or path.startswith(".."):
        return os.path.basename(path)  # outside any repository: the file name only
    rel = path.replace(os.sep, "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _detect_run(notebook: Optional[str], repo: Optional[str]) -> Dict[str, Any]:
    """What a run reports, read by this helper itself. Only these fields leave the computer."""
    top = _git(["rev-parse", "--show-toplevel"])
    info: Dict[str, Any] = {
        "environment": _environment(),
        "helperVersion": __version__,
        "pythonVersion": platform.python_version(),
    }
    path = _repo_relative(notebook or _detect_notebook_path(), top)
    if path and path.lower().endswith(".ipynb"):
        info["notebookPath"] = path
    if top:
        sha = _git(["rev-parse", "HEAD"], cwd=top)
        remote = _normalise_remote(_git(["remote", "get-url", "origin"], cwd=top))
        if sha and _SHA_RE.match(sha):
            info["commitSha"] = sha
            # Porcelain lines are "XY path": the two status columns matter, so no strip.
            status = _git(["status", "--porcelain", "--untracked-files=no"], cwd=top, strip=False) or ""
            changed = [line[3:].strip().strip('"') for line in status.splitlines() if len(line) > 3]
            # A notebook that has been run is always modified: only other files count.
            info["dirty"] = any(f != path for f in changed)
        if remote:
            info["repo"] = remote
    else:
        binder_repo, binder_sha = _binder_ref()
        if binder_repo and binder_sha:
            info["repo"], info["commitSha"], info["dirty"] = binder_repo, binder_sha, False
    if "repo" not in info and repo and re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        info["repo"] = repo  # named by the notebook, no checkout around it
    return info


def start(notebook: Optional[str] = None, repo: Optional[str] = None, url: Optional[str] = None, quiet: bool = False) -> bool:
    """Open a run for this notebook session: connect if needed, then tell Rakiura
    which notebook, repository and commit this is, and where it runs.

    ``notebook`` is the notebook's path inside its repository, for example
    ``"onboarding/tutorial_1.ipynb"``. ``repo`` (``"owner/name"``) is only
    used when there is no git checkout around the notebook (Colab).
    Calling it again in the same session reuses the run.
    """
    _session.quiet = quiet
    if url and _base_url(url) != _session.url:
        _session.key = None
    if not _session.key and not login(url=url, quiet=quiet):
        return False
    info = _detect_run(notebook, repo)
    fingerprint = (_session.url, info.get("notebookPath"), info.get("repo"), info.get("commitSha"), info.get("dirty"))
    if _session.run_id and _session.run_fingerprint == fingerprint:
        return True
    try:
        payload = _request("POST", "/runs", info)
    except RakiuraError as exc:
        return bool(_fail(str(exc)))
    run = payload.get("run") or {}
    _session.run_id = run.get("id")
    _session.run_fingerprint = fingerprint
    _session.run_info = info
    _session.unmapped_said = set()
    lab = (payload.get("lab") or {}).get("name", "Rakiura")
    member = (payload.get("member") or {}).get("name", "you")
    _say(f"rakiura: {member} in {lab}, {_run_phrase(info, run.get('pinVerdict'))}.")
    parts = payload.get("parts") or []
    steps = sorted({p["step"]["displayId"] for p in parts if isinstance(p, dict) and p.get("step")})
    if steps:
        _say(f"rakiura: this notebook counts toward {', '.join(steps)}.")
    elif info.get("notebookPath"):
        _say("rakiura: this notebook isn't linked to a step yet, so tick items on the step page for now.")
    else:
        _say('rakiura: say which notebook this is, for example rakiura.start(notebook="onboarding/tutorial_1.ipynb").')
    return bool(_session.run_id)


def _run_phrase(info: Dict[str, Any], verdict: Optional[str]) -> str:
    """"running your fork at 8f7378c (edited) in Colab", from what start() sent."""
    where = {"local": "on this computer", "colab": "in Colab", "binder": "on Binder"}.get(info["environment"], "here")
    sha = info.get("commitSha")
    if not sha:
        return f"running {where}, no git checkout"
    what = {"pinned": "the pinned commit", "fork_of_pin": "your fork at"}.get(verdict or "", "commit")
    edited = " (edited)" if info.get("dirty") else ""
    return f"running {what} {sha[:7]}{edited} {where}"


def _ensure_run() -> bool:
    if _session.run_id:
        return True
    if _detect_notebook_path() is None:
        _fail('rakiura: run rakiura.start(notebook="path/to/this.ipynb") first, so Rakiura knows which notebook this is.')
        return False
    return start(quiet=_session.quiet)


# ── done: tick, figures, numbers ─────────────────────────────────────────────


def _clean_numbers(numbers: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(numbers, dict) or not 1 <= len(numbers) <= MAX_NUMBERS:
        return None
    out: Dict[str, Any] = {}
    for name, value in numbers.items():
        if not isinstance(name, str):
            return None
        if hasattr(value, "item") and callable(value.item):  # numpy scalars
            try:
                value = value.item()
            except Exception:
                return None
        if isinstance(value, bool) or isinstance(value, str):
            out[name] = value
        elif isinstance(value, (int, float)):
            if value != value or value in (float("inf"), float("-inf")):
                return None
            out[name] = value
        else:
            return None  # never a table, an array or an object
    return out


def done(part: str, figures: Optional[List[Any]] = None, numbers: Optional[Dict[str, Any]] = None, submit: bool = False) -> bool:
    """Say you finished a part: ticks it on its step, and adds any figures and numbers.

    ``part`` is the part's name in this notebook, for example ``"part-2"``;
    Rakiura knows which step it counts toward. ``figures`` takes matplotlib
    figures, ``numbers`` a few named values (``{"peak_ppm": 7.6}``).
    With ``submit=True`` the step is then sent for review, as ``rakiura.submit()``.
    """
    if not isinstance(part, str) or not _PART_RE.match(part.strip()):
        _fail('rakiura: a part name is letters, digits and . _ : - (for example "part-2").')
        return False
    part = part.strip()
    clean_numbers = None
    if numbers is not None:
        clean_numbers = _clean_numbers(numbers)
        if clean_numbers is None:
            _fail(f"rakiura: numbers takes up to {MAX_NUMBERS} named single values (numbers, text or True/False), never a table or an array.")
            return False
    figures = list(figures or [])
    for fig in figures:
        if getattr(fig, "savefig", None) is None:
            _fail("rakiura: figures takes matplotlib figures (for example fig, ax = plt.subplots()).")
            return False
    if not _ensure_run():
        return False
    body: Dict[str, Any] = {"runId": _session.run_id, "checkpoint": part}
    if clean_numbers:
        body["numbers"] = clean_numbers
    try:
        payload = _request("POST", "/checkpoint", body)
    except RakiuraError as exc:
        if _error_code(exc) == "unmapped":
            if part not in _session.unmapped_said:
                _session.unmapped_said.add(part)
                _fail(str(exc))
            return False
        _fail(str(exc))
        return False
    _session.last_part = part
    step = payload.get("deliverable", {})
    item = payload.get("item")
    progress = payload.get("progress") or {}
    tail = f" {progress.get('done')} of {progress.get('total')} done." if progress.get("total") else ""
    if item and payload.get("changed"):
        _say(f"rakiura: ticked \"{item.get('label', part)}\" on {step.get('displayId', '?')}.{tail}")
    elif item:
        _say(f"rakiura: \"{item.get('label', part)}\" was already ticked on {step.get('displayId', '?')}.{tail}")
    else:
        _say(f"rakiura: noted {part} for {step.get('displayId', '?')}.")
    numbers_out = payload.get("numbers")
    if isinstance(numbers_out, dict) and not numbers_out.get("staged") and numbers_out.get("message"):
        _say(f"rakiura: {numbers_out['message']}")
    ok = True
    for i, fig in enumerate(figures, start=1):
        title = f"{part} figure {i}" if len(figures) > 1 else f"{part} figure"
        ok = _stage_figure_body(fig, title, {"runId": _session.run_id, "checkpoint": part}) and ok
    if submit:
        ok = _submit(part=part) and ok
    return ok


def _github_permalink(info: Optional[Dict[str, Any]]) -> Optional[str]:
    """The notebook at its commit on GitHub, only when that commit is pushed and unedited."""
    if not info:
        return None
    repo, sha, path = info.get("repo"), info.get("commitSha"), info.get("notebookPath")
    if not (repo and sha and path) or "/" not in repo or repo.count("/") != 1 or info.get("dirty"):
        return None
    top = _git(["rev-parse", "--show-toplevel"])
    if not top or not _git(["branch", "-r", "--contains", sha], cwd=top):
        return None  # not pushed: the link would not open for anyone else
    return f"https://github.com/{repo}/blob/{sha}/{path}"


def share_notebook() -> Optional[str]:
    """The link to this notebook at its commit on GitHub, once that commit is pushed."""
    link = _github_permalink(_session.run_info or _detect_run(None, None))
    if link is None:
        _fail("rakiura: commit and push your notebook first, so the link opens for your reviewer.")
        return None
    _say(f"rakiura: {link}")
    return link


def submit(step: Optional[str] = None, part: Optional[str] = None, note: Optional[str] = None) -> bool:
    """Send a step for review, with everything you added to it from this notebook.

    Only this call submits; nothing is ever sent for review on its own. With no
    arguments it submits the step of the last ``done(...)`` in this session.
    ``step`` names a step by its id (``"ONB-002-D6"``) instead.
    """
    return _submit(step=step, part=part, note=note)


def _submit(step: Optional[str] = None, part: Optional[str] = None, note: Optional[str] = None) -> bool:
    body: Dict[str, Any] = {}
    if step:
        body["deliverableRef"] = step
        if _session.run_id:
            body["runId"] = _session.run_id
    else:
        part = part or _session.last_part
        if not part:
            _fail("rakiura: say which step to submit, for example rakiura.submit(part=\"exercise-1\").")
            return False
        if not _ensure_run():
            return False
        body["runId"] = _session.run_id
        body["checkpoint"] = part
    if note:
        body["note"] = str(note)[:4000]
    link = _github_permalink(_session.run_info)
    if link:
        body["notebookUrl"] = link
    try:
        payload = _request("POST", "/submit", body)
    except RakiuraError as exc:
        _fail(str(exc))
        return False
    d = payload.get("deliverable", {})
    n = payload.get("attached", 0)
    things = "1 item" if n == 1 else f"{n} items"
    _say(f"rakiura: sent {d.get('displayId', '?')} for review with {things}. See it here: {d.get('url', '')}")
    return True


# ── reads ────────────────────────────────────────────────────────────────────


def me(show: bool = True) -> Optional[Dict[str, Any]]:
    """Who this key is, and every step you can log against, with cell tags.

    Prints a short listing (unless ``show=False``) and returns the payload.
    """
    try:
        payload = _request("GET", "/me")
    except RakiuraError as exc:
        return _fail(str(exc))
    if show:
        lab = payload.get("lab", {}).get("name", "?")
        member = payload.get("member", {}).get("name", "?")
        _say(f"rakiura: {member} in {lab}")
        for d in payload.get("deliverables", []):
            flag = "  (can stage figures)" if d.get("canStageArtifacts") else ""
            _say(f"  {d['displayId']}  {d['name']}{flag}")
            for item in d.get("items", []):
                mark = "x" if item.get("checked") else " "
                _say(f"    [{mark}] {item['tag']}  {item['label']}")
    return payload


# ── checkpoints by cell tag (0.1) ────────────────────────────────────────────


def checkpoint(tag: str, status: str = "done") -> bool:
    """Tick (``status="done"``) or untick (``"undone"``) one checklist item.

    ``tag`` is ``<STEP-ID>/<item-key>``, e.g. ``"ONB-001-D4/part-1"``.
    Idempotent: running the cell twice is fine. Returns True when the item is
    now in the requested state, False otherwise (after printing why).
    """
    if not isinstance(tag, str) or "/" not in tag:
        _fail("rakiura: a cell tag looks like STEP-ID/item-key, for example ONB-001-D4/part-1. Run rakiura.me() to see yours.")
        return False
    try:
        payload = _request("POST", "/checkpoint", {"cellTag": tag.strip(), "status": status})
    except RakiuraError as exc:
        _fail(str(exc))
        return False
    item = payload.get("item") or {}
    step = payload.get("deliverable", {})
    if payload.get("changed"):
        verb = "ticked" if payload.get("checked") else "unticked"
        _say(f"rakiura: {verb} \"{item.get('label', tag)}\" on {step.get('displayId', '?')}.")
    else:
        _say(f"rakiura: \"{item.get('label', tag)}\" was already {'ticked' if payload.get('checked') else 'unticked'}.")
    return True


def uncheck(tag: str) -> bool:
    """Untick one checklist item (the opposite of ``checkpoint``)."""
    return checkpoint(tag, status="undone")


def checkpoint_cell(tag: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: tick ``tag`` after the function runs without raising.

    ::

        @rakiura.checkpoint_cell("ONB-001-D4/part-1")
        def run_first_binary():
            ...

    The function's own exception still propagates (that is your code); only
    the checkpoint is silent.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            checkpoint(tag)
            return result

        return wrapper

    return decorate


# ── staged artifacts ─────────────────────────────────────────────────────────


def _stage(body: Dict[str, Any]) -> bool:
    if _session.run_id and "runId" not in body:
        body = {**body, "runId": _session.run_id}
    try:
        payload = _request("POST", "/artifacts", body)
    except RakiuraError as exc:
        if _error_code(exc) == "unmapped" and body.get("checkpoint") in _session.unmapped_said:
            return False
        _fail(str(exc))
        return False
    step = payload.get("deliverable", {})
    title = payload.get("artifact", {}).get("title", body.get("title", ""))
    if payload.get("created"):
        _say(f"rakiura: added \"{title}\" to {step.get('displayId', '?')}. It goes in when you submit: {step.get('url', '')}")
    else:
        _say(f"rakiura: \"{title}\" was already added to {step.get('displayId', '?')}.")
    return True


def stage_link(url: str, title: str, step: str, cell: Optional[str] = None) -> bool:
    """Stage a link (your notebook on GitHub, a shared folder) for ``step``."""
    if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        _fail("rakiura: stage_link needs an absolute http(s) link.")
        return False
    body: Dict[str, Any] = {"deliverableRef": step, "title": title, "url": url}
    if cell:
        body["cellTag"] = cell
    return _stage(body)


def stage_text(text: str, title: str, step: str, mime: str = "text/plain", cell: Optional[str] = None) -> bool:
    """Stage a small text or CSV (up to 50 KB) for ``step``."""
    if mime not in ("text/plain", "text/csv"):
        _fail("rakiura: stage_text takes mime text/plain or text/csv.")
        return False
    if len(text.encode("utf-8")) > FILE_TEXT_MAX_BYTES:
        _fail(f"rakiura: that text is over {FILE_TEXT_MAX_BYTES // 1024} KB. Save it somewhere and stage_link it instead.")
        return False
    body: Dict[str, Any] = {"deliverableRef": step, "title": title, "fileText": text, "mimeType": mime}
    if cell:
        body["cellTag"] = cell
    return _stage(body)


def stage_figure(fig: Any, title: str, step: str, cell: Optional[str] = None) -> bool:
    """Stage a matplotlib figure (or anything with ``savefig``) as a PNG for ``step``.

    The figure is rendered at a modest size; if it still exceeds 50 KB, save
    it yourself and ``stage_link`` it instead. Returns True when staged.

    Pass the figure you drew on (``fig, ax = plt.subplots()``). ``plt.gcf()``
    after ``plt.show()`` is a new, empty figure, so it is refused rather than
    staged as a blank image.
    """
    target: Dict[str, Any] = {"deliverableRef": step}
    if cell:
        target["cellTag"] = cell
    return _stage_figure_body(fig, title, target)


def _stage_figure_body(fig: Any, title: str, target: Dict[str, Any]) -> bool:
    axes = getattr(fig, "axes", None)
    if axes is not None and len(axes) == 0:
        _fail("rakiura: that figure is empty, so nothing was staged. After plt.show(), plt.gcf() is a new blank figure: pass the one you drew on, for example fig, ax = plt.subplots().")
        return False
    png = _render_png(fig)
    if png is None:
        _fail("rakiura: stage_figure needs a matplotlib Figure (or an object with .savefig).")
        return False
    encoded = base64.b64encode(png).decode("ascii")
    if len(encoded) > FILE_TEXT_MAX_BYTES:
        _fail(f"rakiura: the figure is over {FILE_TEXT_MAX_BYTES // 1024} KB even at a small size. Save it with fig.savefig() and stage_link it instead.")
        return False
    body: Dict[str, Any] = {**target, "title": title, "fileText": encoded, "mimeType": "image/png"}
    return _stage(body)


def _fits(data: bytes) -> bool:
    return len(base64.b64encode(data)) <= FILE_TEXT_MAX_BYTES


def _palette_png(data: bytes) -> Optional[bytes]:
    """The same image as a 128-colour palette PNG, or None without Pillow.

    Plots are flat colours on white, so a palette PNG is typically three to
    four times smaller than matplotlib's full-colour one at the same size.
    That is what keeps a multi-panel figure readable under the 50 KB cap
    instead of shrinking it until the labels blur. Pillow ships with
    matplotlib; anything that goes wrong here just means "no smaller copy".
    """
    try:
        from PIL import Image  # type: ignore

        image = Image.open(io.BytesIO(data)).convert("RGB")
        palette = image.quantize(colors=128)
        out = io.BytesIO()
        palette.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return None


def _render_png(fig: Any) -> Optional[bytes]:
    savefig = getattr(fig, "savefig", None)
    if savefig is None:
        return None
    # Largest legible size first; at each size try the full-colour PNG, then
    # its palette copy, and only then go smaller.
    for dpi in (100, 72, 50, 36):
        buf = io.BytesIO()
        try:
            savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
        except TypeError:
            try:
                savefig(buf, format="png")
            except Exception:
                return None
        except Exception:
            return None
        data = buf.getvalue()
        if _fits(data):
            return data
        smaller = _palette_png(data)
        if smaller is not None and _fits(smaller):
            return smaller
    return data  # over the cap; the caller says so


# ── small conveniences ───────────────────────────────────────────────────────


def _tags(payload: Optional[Dict[str, Any]]) -> List[str]:
    if not payload:
        return []
    return [item["tag"] for d in payload.get("deliverables", []) for item in d.get("items", [])]


if __name__ == "__main__":  # pragma: no cover
    sys.exit("rakiura is a library: import it from a notebook.")
