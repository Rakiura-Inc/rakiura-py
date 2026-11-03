"""done() and submit(): tick a part of the notebook on its step, and send a step for review."""

import math
import re
from typing import Any

from ._api import call
from ._artifacts import stage_figure_body
from ._config import MAX_NUMBERS, NOTE_MAX_CHARS
from ._runs import ensure_run, github_permalink
from ._session import RakiuraError, fail, say, session

# A part name as notebooks write it: "part-2", "exercise-1", "fit:linear".
PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_REFUSED = object()


def _as_scalar(value: Any) -> Any:
    """``value`` as a plain bool, str, int or float (numpy scalars included), or ``_REFUSED``."""
    if hasattr(value, "item") and callable(value.item):  # numpy scalars
        try:
            value = value.item()
        except Exception:  # noqa: BLE001 - an .item() that fails is not a single value
            return _REFUSED
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
        return value
    return _REFUSED  # never a table, an array, an object, NaN or infinity


def clean_numbers(numbers: Any) -> dict[str, Any] | None:
    """Up to MAX_NUMBERS named single values, ready to send; None if anything else is in there."""
    if not isinstance(numbers, dict) or not 1 <= len(numbers) <= MAX_NUMBERS:
        return None
    out: dict[str, Any] = {}
    for name, value in numbers.items():
        scalar = _as_scalar(value)
        if not isinstance(name, str) or scalar is _REFUSED:
            return None
        out[name] = scalar
    return out


def done(
    part: str,
    figures: list[Any] | None = None,
    numbers: dict[str, Any] | None = None,
    submit: bool = False,
) -> bool:
    """Say you finished a part: ticks it on its step, and adds any figures and numbers.

    ``part`` is the part's name in this notebook, for example ``"part-2"``;
    Rakiura knows which step it counts toward. ``figures`` takes matplotlib
    figures, ``numbers`` a few named values (``{"peak_ppm": 7.6}``).
    With ``submit=True`` the step is then sent for review, as ``rakiura.submit()``.
    """
    if not isinstance(part, str) or not PART_RE.match(part.strip()):
        fail('rakiura: a part name is letters, digits and . _ : - (for example "part-2").')
        return False
    part = part.strip()
    numbers_to_send = None
    if numbers is not None:
        numbers_to_send = clean_numbers(numbers)
        if numbers_to_send is None:
            fail(
                f"rakiura: numbers takes up to {MAX_NUMBERS} named single values (numbers, text or True/False), "
                "never a table or an array."
            )
            return False
    figures = list(figures or [])
    if any(getattr(fig, "savefig", None) is None for fig in figures):
        fail("rakiura: figures takes matplotlib figures (for example fig, ax = plt.subplots()).")
        return False
    if not ensure_run():
        return False
    if not _tick(part, numbers_to_send):
        return False
    ok = True
    for i, fig in enumerate(figures, start=1):
        title = f"{part} figure {i}" if len(figures) > 1 else f"{part} figure"
        ok = stage_figure_body(fig, title, {"runId": session.run_id, "checkpoint": part}) and ok
    if submit:
        ok = _submit(part=part) and ok
    return ok


def _tick(part: str, numbers: dict[str, Any] | None) -> bool:
    """Tick ``part`` in the current run, with any numbers, and say what happened."""
    body: dict[str, Any] = {"runId": session.run_id, "checkpoint": part}
    if numbers:
        body["numbers"] = numbers
    try:
        payload = call("POST", "/checkpoint", body)
    except RakiuraError as exc:
        # An unlinked part is said once per run: done() is often in a loop.
        unmapped = exc.code == "unmapped"
        if not (unmapped and part in session.unmapped_said):
            fail(str(exc))
        if unmapped:
            session.unmapped_said.add(part)
        return False
    session.last_part = part
    step_id = payload.get("deliverable", {}).get("displayId", "?")
    item = payload.get("item")
    progress = payload.get("progress") or {}
    tail = f" {progress.get('done')} of {progress.get('total')} done." if progress.get("total") else ""
    if item and payload.get("changed"):
        say(f'rakiura: ticked "{item.get("label", part)}" on {step_id}.{tail}')
    elif item:
        say(f'rakiura: "{item.get("label", part)}" was already ticked on {step_id}.{tail}')
    else:
        say(f"rakiura: noted {part} for {step_id}.")
    numbers_reply = payload.get("numbers")
    if isinstance(numbers_reply, dict) and not numbers_reply.get("staged") and numbers_reply.get("message"):
        say(f"rakiura: {numbers_reply['message']}")
    return True


def submit(step: str | None = None, part: str | None = None, note: str | None = None) -> bool:
    """Send a step for review, with everything you added to it from this notebook.

    Only this call submits; nothing is ever sent for review on its own. With no
    arguments it submits the step of the last ``done(...)`` in this session.
    ``step`` names a step by its id (``"ONB-002-D6"``) instead.
    """
    return _submit(step=step, part=part, note=note)


def _submit(step: str | None = None, part: str | None = None, note: str | None = None) -> bool:
    body: dict[str, Any] = {}
    if step:
        body["deliverableRef"] = step
        if session.run_id:
            body["runId"] = session.run_id
    else:
        part = part or session.last_part
        if not part:
            fail('rakiura: say which step to submit, for example rakiura.submit(part="exercise-1").')
            return False
        if not ensure_run():
            return False
        body["runId"] = session.run_id
        body["checkpoint"] = part
    if note:
        body["note"] = str(note)[:NOTE_MAX_CHARS]
    link = github_permalink(session.run_info)
    if link:
        body["notebookUrl"] = link
    try:
        payload = call("POST", "/submit", body)
    except RakiuraError as exc:
        fail(str(exc))
        return False
    step_info = payload.get("deliverable", {})
    attached = payload.get("attached", 0)
    things = "1 item" if attached == 1 else f"{attached} items"
    say(
        f"rakiura: sent {step_info.get('displayId', '?')} for review with {things}. "
        f"See it here: {step_info.get('url', '')}"
    )
    return True
