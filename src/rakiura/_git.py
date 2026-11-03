"""Reading the git checkout around the notebook. Never writes to it."""

import subprocess
from pathlib import Path

from ._config import GIT_TIMEOUT_SECONDS


def git(args: list[str], cwd: str | Path | None = None, *, strip: bool = True) -> str | None:
    """One git command's stdout (stripped unless ``strip=False``), or None (no git, not a checkout, error)."""
    try:
        out = subprocess.run(  # noqa: S603 - a fixed git argv built here, never a shell
            ["git", *args],  # noqa: S607 - the user's own git, wherever PATH has it
            cwd=cwd or Path.cwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # noqa: BLE001 - no git, a timeout or a missing folder all mean "no answer"
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.decode("utf-8", "replace")
    return text.strip() if strip else text


def toplevel(cwd: str | Path | None = None) -> str | None:
    """The root of the checkout around ``cwd`` (default: the kernel's folder), or None."""
    return git(["rev-parse", "--show-toplevel"], cwd=cwd)


def is_inside(path: str | Path, folder: str | Path) -> bool:
    """Is ``path`` the folder itself or anything under it, after following symlinks?"""
    return Path(path).resolve().is_relative_to(Path(folder).resolve())
