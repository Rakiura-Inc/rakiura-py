"""What the kernel can see about where it runs. Only the conclusions leave the computer, never the raw values."""

import contextlib
import os
import re
import sys

BINDER_VARIABLES = ("BINDER_SERVICE_HOST", "BINDER_REPO_URL", "BINDER_LAUNCH_HOST", "BINDER_REF_URL")
_BINDER_REF_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/tree/([0-9a-f]{40})")


def environment() -> str:
    """``local``, ``colab`` or ``binder``."""
    if "google.colab" in sys.modules or os.environ.get("COLAB_RELEASE_TAG"):
        return "colab"
    if any(os.environ.get(name) for name in BINDER_VARIABLES):
        return "binder"
    return "local"


def binder_ref() -> tuple[str | None, str | None]:
    """(repo, sha) from Binder's own variables, when this is Binder without a .git folder."""
    match = _BINDER_REF_RE.match(os.environ.get("BINDER_REF_URL") or "")
    return (match.group(1), match.group(2)) if match else (None, None)


def detect_notebook_path() -> str | None:
    """The running notebook's path, when Jupyter or VS Code says so."""
    candidate = os.environ.get("JPY_SESSION_NAME")
    with contextlib.suppress(Exception):  # no IPython, or one without user_ns: nothing to detect
        from IPython import get_ipython  # IPython is only there inside a notebook kernel

        shell = get_ipython()
        if shell is not None:
            candidate = shell.user_ns.get("__vsc_ipynb_file__") or candidate
    return candidate if candidate and candidate.endswith(".ipynb") else None
