"""Connecting a notebook session to Rakiura: a key from somewhere, or the one-click device sign-in."""

import contextlib
import getpass
import html
import os
import time
import webbrowser
from http import HTTPStatus
from typing import Any

from ._config import KEY_PREFIX, base_url, url_allowed
from ._credentials import colab_secret, forget_saved_key, save_key, saved_key
from ._environment import environment
from ._session import RakiuraError, fail, say, session
from ._transport import Payload, error_code, error_message, public_post, public_post_raw
from ._version import __version__

# Used when the server leaves them out of its device-flow reply.
DEFAULT_POLL_SECONDS = 5
DEFAULT_CODE_LIFETIME_SECONDS = 600
# RFC 8628: on "slow_down", wait this much longer between polls from then on.
SLOW_DOWN_STEP_SECONDS = 5

# Key sources whose key lives in the credentials file.
SAVED_SOURCES = ("saved", "device")


def login(
    key: str | None = None,
    url: str | None = None,
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
    session.quiet = quiet
    target = base_url(url)
    if target is None:
        session.key = None
        fail("rakiura: the Rakiura address must start with https://. No key was set.")
        return False
    if target != session.url:
        # A key belongs to the address it came from: never carry it to another
        # one, even if the sign-in below fails.
        session.forget_key()
        session.forget_run()
    session.url = target

    if key:
        say(
            "rakiura: a key written in a notebook can leak when the notebook is shared. "
            "Run rakiura.login() with no key to connect with one click instead."
        )
        return _set_key(key, "argument")
    for source, candidate in (("env", os.environ.get("RAKIURA_KEY")), ("colab", colab_secret())):
        if candidate:
            return _set_key(candidate, source)
    saved = saved_key(target)
    if saved:
        session.key = saved
        session.key_source = "saved"
        return True
    return _device_login(target, remember=remember)


def logout(forget: bool = True) -> None:
    """Forget the key for this session and, with ``forget``, the one saved on this computer."""
    if forget and session.key_source in SAVED_SOURCES:
        forget_saved_key(session.url)
    session.forget_key()
    session.forget_run()
    session.run_info = None
    session.last_part = None


def _set_key(candidate: str, source: str) -> bool:
    candidate = (candidate or "").strip()
    if not candidate.startswith(KEY_PREFIX):
        session.key = None
        fail(
            "rakiura: that does not look like a notebook key (they start with rknb_). "
            "Run rakiura.login() with no key to connect with one click."
        )
        return False
    session.key = candidate
    session.key_source = source
    say("rakiura: key set for this session.")
    return True


def _show_link(link: str, code: str) -> None:
    """The sign-in link as one printed line, clickable in Jupyter and Colab too."""
    say(f"rakiura: open {link} and click Allow (check the code shows {code}).")
    if session.quiet:
        return
    # The printed line is enough on its own: a missing IPython or browser changes nothing.
    with contextlib.suppress(Exception):
        from IPython import get_ipython  # IPython is only there inside a notebook kernel
        from IPython.display import HTML, display

        if get_ipython() is not None:
            safe_link, safe_code = html.escape(link, quote=True), html.escape(code)
            anchor = f'<a href="{safe_link}" target="_blank" rel="noopener">Connect this notebook to Rakiura</a>'
            display(HTML(f"{anchor} (code {safe_code})"))
    if environment() == "local" and not os.environ.get("RAKIURA_NO_BROWSER"):
        with contextlib.suppress(Exception):
            webbrowser.open(link, new=2)


def _device_login(url: str, *, remember: bool) -> bool:
    """The one-click sign-in: print a link, wait while the user clicks Allow in Rakiura."""
    try:
        started = public_post(url, "/device", {"environment": environment(), "helperVersion": __version__})
    except RakiuraError as exc:
        if exc.status == HTTPStatus.NOT_FOUND:  # a Rakiura without one-click sign-in
            return _prompt_for_key()
        fail(str(exc))
        return False
    device_code = started.get("deviceCode")
    link = started.get("verificationUriComplete") or started.get("verificationUri")
    if not isinstance(device_code, str) or not isinstance(link, str) or not url_allowed(link):
        fail(f"rakiura: unexpected reply from {url}.")
        return False
    _show_link(link, started.get("userCode", ""))
    interval = max(1, int(started.get("interval") or DEFAULT_POLL_SECONDS))
    lifetime = int(started.get("expiresIn") or DEFAULT_CODE_LIFETIME_SECONDS)
    try:
        return _wait_for_allow(url, device_code, interval, lifetime, remember=remember)
    except KeyboardInterrupt:
        fail("rakiura: sign-in stopped. Run rakiura.login() to try again.")
    except RakiuraError as exc:
        fail(str(exc))
    return False


def _wait_for_allow(url: str, device_code: str, interval: int, lifetime: int, *, remember: bool) -> bool:
    """Poll until the user clicks Allow (True), says no or the code expires (False)."""
    deadline = time.monotonic() + lifetime
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, payload = public_post_raw(url, "/device/token", {"deviceCode": device_code})
        if status == HTTPStatus.OK and payload.get("ok") and isinstance(payload.get("key"), str):
            if not payload["key"].startswith(KEY_PREFIX):
                break
            return _connected(url, payload, remember=remember)
        code = error_code(payload)
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += SLOW_DOWN_STEP_SECONDS
            continue
        fail(f"rakiura: {error_message(payload, status)}")
        return False
    fail("rakiura: the sign-in code expired. Run rakiura.login() again for a new one.")
    return False


def _connected(url: str, payload: Payload, *, remember: bool) -> bool:
    """Take the key the device flow returned, save it unless asked not to, and say who is connected."""
    key = payload["key"]
    session.key = key
    session.key_source = "device"
    lab: dict[str, Any] = payload.get("lab") or {}
    member: dict[str, Any] = payload.get("member") or {}
    saved = remember and save_key(
        url,
        key,
        expires_at=payload.get("expiresAt"),
        label=payload.get("label"),
        lab=lab.get("name"),
    )
    where = " This computer remembers it for 30 days." if saved else ""
    say(f"rakiura: connected as {member.get('name', 'you')} in {lab.get('name', 'Rakiura')}.{where}")
    return True


def _prompt_for_key() -> bool:
    """For a Rakiura that has no one-click sign-in: ask for a key, hidden."""
    try:
        candidate = getpass.getpass("Rakiura notebook key (from Settings, Notebook key): ")
    except Exception:  # noqa: BLE001 - no terminal, no prompt
        candidate = None
    if not (candidate or "").strip():
        fail("rakiura: no key given. Set RAKIURA_KEY, or create one in Rakiura under Settings, Notebook key.")
        return False
    return _set_key(candidate or "", "prompt")
