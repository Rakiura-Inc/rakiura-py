"""What this notebook session remembers, and the one way the helper talks to the notebook: a printed line."""

from dataclasses import dataclass, field
from typing import Any

from ._config import DEFAULT_URL


class RakiuraError(Exception):
    """Raised ONLY by the private request helpers; public calls catch it."""

    def __init__(self, message: str, code: str | None = None, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class Session:
    """The connection and the current run. One per kernel (``rakiura._session``)."""

    key: str | None = None
    url: str = DEFAULT_URL
    quiet: bool = False
    # Where the key came from: argument | env | colab | saved | device | prompt
    key_source: str | None = None
    # The current run (rakiura.start()), and what it was opened for.
    run_id: str | None = None
    run_fingerprint: tuple[Any, ...] | None = None
    run_info: dict[str, Any] | None = None
    last_part: str | None = None
    unmapped_said: set[str] = field(default_factory=set)
    # The last error, for people who want to look (never printed twice).
    last_error: str | None = None

    def forget_key(self) -> None:
        self.key = None
        self.key_source = None

    def forget_run(self) -> None:
        self.run_id = None
        self.run_fingerprint = None


session = Session()


def say(line: str) -> None:
    """One line, to stdout, unless the caller asked for silence."""
    if not session.quiet:
        print(line)  # noqa: T201 - printing one line into the notebook is how the helper reports


def fail(line: str) -> None:
    """Say ``line`` and keep it as the session's last error."""
    session.last_error = line
    say(line)
