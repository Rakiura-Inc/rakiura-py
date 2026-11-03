"""Fixed values, and which Rakiura addresses the key may travel to."""

import os
from urllib.parse import urlsplit

from ._version import __version__

DEFAULT_URL = "https://labs.rakiura.co"
API_PREFIX = "/api/notebook/v1"
KEY_PREFIX = "rknb_"
USER_AGENT = f"rakiura-py/{__version__}"

# Limits the server enforces too; checking them here saves a round trip.
FILE_TEXT_MAX_BYTES = 50 * 1024
MAX_NUMBERS = 20
NOTE_MAX_CHARS = 4000

HTTP_TIMEOUT_SECONDS = 15
GIT_TIMEOUT_SECONDS = 5

# Plain http is allowed only to these, for testing against a local Rakiura.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def url_allowed(url: str) -> bool:
    """https anywhere; plain http only to this computer."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def expand_user(path: str) -> str:
    """``~`` expanded. Unlike Path.expanduser, never raises when there is no home folder."""
    return os.path.expanduser(path)  # noqa: PTH111 - see the docstring


def base_url(url: str | None) -> str | None:
    """The Rakiura address to use (argument, then ``RAKIURA_URL``, then the default), or None if not allowed."""
    target = (url or os.environ.get("RAKIURA_URL") or DEFAULT_URL).rstrip("/")
    return target if url_allowed(target) else None
