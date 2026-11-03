"""One HTTP request to Rakiura: JSON in, JSON out, redirects never followed.

``requests`` is used when it is installed, otherwise ``urllib``; neither is
required. Every failure becomes a :class:`RakiuraError` with a one-line message.
"""

import json
import urllib.error
import urllib.request
from typing import Any

from ._config import API_PREFIX, HTTP_TIMEOUT_SECONDS, USER_AGENT
from ._session import RakiuraError, session

Payload = dict[str, Any]

FIRST_REDIRECT_STATUS = 300
FIRST_ERROR_STATUS = 400

BASE_HEADERS = {"Accept": "application/json", "User-Agent": USER_AGENT}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand a 3xx back to the caller instead of following it (urllib then raises HTTPError with it)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send_with_requests(requests: Any, method: str, url: str, headers: dict[str, str], data: bytes | None):
    # An explicit (no-op) auth stops requests from replacing the key's
    # Authorization header with a ~/.netrc entry; proxies still apply.
    resp = requests.request(
        method,
        url,
        headers=headers,
        data=data,
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
        auth=lambda r: r,
    )
    return resp.status_code, resp.text


def _send_with_urllib(method: str, url: str, headers: dict[str, str], data: bytes | None):
    opener = urllib.request.build_opener(_NoRedirect())
    # The caller only ever passes an address url_allowed() accepted: https, or http to this computer.
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - see above
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as http_err:
        return http_err.code, http_err.read().decode("utf-8", "replace")


def _send(method: str, url: str, headers: dict[str, str], data: bytes | None) -> tuple[int, str]:
    """One request, redirects NOT followed (a 3xx comes back as its status)."""
    try:
        import requests  # optional, and looked up on each call
    except ImportError:
        return _send_with_urllib(method, url, headers, data)
    return _send_with_requests(requests, method, url, headers, data)


def error_message(payload: Payload, status: int) -> str:
    """The server's own message, or the bare HTTP status."""
    err = payload.get("error")
    return (err.get("message") if isinstance(err, dict) else None) or f"HTTP {status}"


def error_code(payload: Payload) -> str | None:
    err = payload.get("error")
    return err.get("code") if isinstance(err, dict) else None


def exchange(method: str, url: str, headers: dict[str, str], body: Payload | None) -> tuple[int, Payload]:
    """Send ``body`` as JSON; return the status and the JSON object that came back."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    try:
        status, text = _send(method, url, headers, data)
    except Exception as exc:  # offline, DNS, refused, timeout: one line, no traceback
        msg = (
            f"rakiura: could not reach {session.url} ({exc.__class__.__name__}). "
            "Your work is fine; tick the box on the step page instead."
        )
        raise RakiuraError(msg) from exc
    if FIRST_REDIRECT_STATUS <= status < FIRST_ERROR_STATUS:
        msg = (
            f"rakiura: {session.url} answered with a redirect, so nothing was sent on. "
            "Check the address you gave rakiura.login()."
        )
        raise RakiuraError(msg, status=status)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        msg = f"rakiura: unexpected reply from {session.url} (HTTP {status})."
        raise RakiuraError(msg, status=status)
    return status, payload


def public_post_raw(url: str, path: str, body: Payload) -> tuple[int, Payload]:
    """A POST with no key (the sign-in endpoints), whatever the status."""
    return exchange("POST", f"{url}{API_PREFIX}{path}", BASE_HEADERS, body)


def public_post(url: str, path: str, body: Payload) -> Payload:
    """A POST with no key that must succeed."""
    status, payload = public_post_raw(url, path, body)
    if status >= FIRST_ERROR_STATUS or not payload.get("ok"):
        msg = f"rakiura: {error_message(payload, status)}"
        raise RakiuraError(msg, error_code(payload), status=status)
    return payload


def keyed_request(method: str, path: str, body: Payload | None) -> tuple[int, Payload]:
    """A request to this session's Rakiura carrying this session's key."""
    headers = {**BASE_HEADERS, "Authorization": f"Bearer {session.key}"}
    return exchange(method, f"{session.url}{API_PREFIX}{path}", headers, body)
