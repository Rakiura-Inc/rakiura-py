"""The 0.1 interface, by cell tag (``STEP-ID/item-key``). Still supported; done() is the 0.2 way."""

import functools
from collections.abc import Callable
from typing import Any

from ._api import call
from ._session import RakiuraError, fail, say


def me(show: bool = True) -> dict[str, Any] | None:
    """Who this key is, and every step you can log against, with cell tags.

    Prints a short listing (unless ``show=False``) and returns the payload.
    """
    try:
        payload = call("GET", "/me")
    except RakiuraError as exc:
        fail(str(exc))
        return None
    if show:
        _say_listing(payload)
    return payload


def _say_listing(payload: dict[str, Any]) -> None:
    lab = payload.get("lab", {}).get("name", "?")
    member = payload.get("member", {}).get("name", "?")
    say(f"rakiura: {member} in {lab}")
    for step in payload.get("deliverables", []):
        flag = "  (can stage figures)" if step.get("canStageArtifacts") else ""
        say(f"  {step['displayId']}  {step['name']}{flag}")
        for item in step.get("items", []):
            mark = "x" if item.get("checked") else " "
            say(f"    [{mark}] {item['tag']}  {item['label']}")


def checkpoint(tag: str, status: str = "done") -> bool:
    """Tick (``status="done"``) or untick (``"undone"``) one checklist item.

    ``tag`` is ``<STEP-ID>/<item-key>``, e.g. ``"ONB-001-D4/part-1"``.
    Idempotent: running the cell twice is fine. Returns True when the item is
    now in the requested state, False otherwise (after printing why).
    """
    if not isinstance(tag, str) or "/" not in tag:
        fail(
            "rakiura: a cell tag looks like STEP-ID/item-key, for example ONB-001-D4/part-1. "
            "Run rakiura.me() to see yours."
        )
        return False
    try:
        payload = call("POST", "/checkpoint", {"cellTag": tag.strip(), "status": status})
    except RakiuraError as exc:
        fail(str(exc))
        return False
    label = (payload.get("item") or {}).get("label", tag)
    state = "ticked" if payload.get("checked") else "unticked"
    if payload.get("changed"):
        say(f'rakiura: {state} "{label}" on {payload.get("deliverable", {}).get("displayId", "?")}.')
    else:
        say(f'rakiura: "{label}" was already {state}.')
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
