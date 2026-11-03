"""Adding a link, a small text file or a figure to a step. They go in with the next submission."""

import base64
from typing import Any

from ._api import call
from ._config import FILE_TEXT_MAX_BYTES
from ._figures import render_png
from ._session import RakiuraError, fail, say, session

TEXT_MIME_TYPES = ("text/plain", "text/csv")


def stage(body: dict[str, Any]) -> bool:
    """POST one artifact, tied to the current run when there is one, and say where it went."""
    if session.run_id and "runId" not in body:
        body = {**body, "runId": session.run_id}
    try:
        payload = call("POST", "/artifacts", body)
    except RakiuraError as exc:
        # done() already said once that this part is not linked to a step.
        if exc.code == "unmapped" and body.get("checkpoint") in session.unmapped_said:
            return False
        fail(str(exc))
        return False
    step = payload.get("deliverable", {})
    title = payload.get("artifact", {}).get("title", body.get("title", ""))
    if payload.get("created"):
        where = step.get("displayId", "?")
        say(f'rakiura: added "{title}" to {where}. It goes in when you submit: {step.get("url", "")}')
    else:
        say(f'rakiura: "{title}" was already added to {step.get("displayId", "?")}.')
    return True


def stage_link(url: str, title: str, step: str, cell: str | None = None) -> bool:
    """Stage a link (your notebook on GitHub, a shared folder) for ``step``."""
    if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        fail("rakiura: stage_link needs an absolute http(s) link.")
        return False
    body: dict[str, Any] = {"deliverableRef": step, "title": title, "url": url}
    if cell:
        body["cellTag"] = cell
    return stage(body)


def stage_text(text: str, title: str, step: str, mime: str = "text/plain", cell: str | None = None) -> bool:
    """Stage a small text or CSV (up to 50 KB) for ``step``."""
    if mime not in TEXT_MIME_TYPES:
        fail("rakiura: stage_text takes mime text/plain or text/csv.")
        return False
    if len(text.encode("utf-8")) > FILE_TEXT_MAX_BYTES:
        fail(
            f"rakiura: that text is over {FILE_TEXT_MAX_BYTES // 1024} KB. Save it somewhere and stage_link it instead."
        )
        return False
    body: dict[str, Any] = {"deliverableRef": step, "title": title, "fileText": text, "mimeType": mime}
    if cell:
        body["cellTag"] = cell
    return stage(body)


def stage_figure(fig: Any, title: str, step: str, cell: str | None = None) -> bool:
    """Stage a matplotlib figure (or anything with ``savefig``) as a PNG for ``step``.

    The figure is rendered at a modest size; if it still exceeds 50 KB, save
    it yourself and ``stage_link`` it instead. Returns True when staged.

    Pass the figure you drew on (``fig, ax = plt.subplots()``). ``plt.gcf()``
    after ``plt.show()`` is a new, empty figure, so it is refused rather than
    staged as a blank image.
    """
    target: dict[str, Any] = {"deliverableRef": step}
    if cell:
        target["cellTag"] = cell
    return stage_figure_body(fig, title, target)


def stage_figure_body(fig: Any, title: str, target: dict[str, Any]) -> bool:
    """Render ``fig`` and stage it with ``target`` (a step, or a run and part)."""
    axes = getattr(fig, "axes", None)
    if axes is not None and len(axes) == 0:
        fail(
            "rakiura: that figure is empty, so nothing was staged. After plt.show(), plt.gcf() is a new blank "
            "figure: pass the one you drew on, for example fig, ax = plt.subplots()."
        )
        return False
    png = render_png(fig)
    if png is None:
        fail("rakiura: stage_figure needs a matplotlib Figure (or an object with .savefig).")
        return False
    encoded = base64.b64encode(png).decode("ascii")
    if len(encoded) > FILE_TEXT_MAX_BYTES:
        fail(
            f"rakiura: the figure is over {FILE_TEXT_MAX_BYTES // 1024} KB even at a small size. "
            "Save it with fig.savefig() and stage_link it instead."
        )
        return False
    return stage({**target, "title": title, "fileText": encoded, "mimeType": "image/png"})
