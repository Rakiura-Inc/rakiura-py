"""Turning a matplotlib figure into a PNG small enough to stage, while keeping it legible."""

import base64
import io
from collections.abc import Callable
from typing import Any

from ._config import FILE_TEXT_MAX_BYTES

# Largest legible size first; only go smaller when nothing at this size fits.
DPI_STEPS = (100, 72, 50, 36)
# Plots are flat colours on white, so 128 colours lose nothing visible.
PALETTE_COLOURS = 128


def _fits(data: bytes) -> bool:
    """Is ``data`` within the staging cap once base64-encoded (as it travels)?"""
    return len(base64.b64encode(data)) <= FILE_TEXT_MAX_BYTES


def _palette_png(data: bytes) -> bytes | None:
    """The same image as a 128-colour palette PNG, or None without Pillow.

    A palette PNG is typically three to four times smaller than matplotlib's
    full-colour one at the same size. That is what keeps a multi-panel figure
    readable under the 50 KB cap instead of shrinking it until the labels blur.
    Pillow ships with matplotlib; anything that goes wrong here just means "no
    smaller copy".
    """
    try:
        from PIL import Image  # Pillow is optional

        image = Image.open(io.BytesIO(data)).convert("RGB")
        out = io.BytesIO()
        image.quantize(colors=PALETTE_COLOURS).save(out, format="PNG", optimize=True)
    except Exception:  # noqa: BLE001 - see the docstring
        return None
    return out.getvalue()


def _save_png(savefig: Callable[..., Any], dpi: int) -> bytes | None:
    """One PNG from ``savefig`` at ``dpi``, or None when it cannot draw one."""
    buf = io.BytesIO()
    try:
        savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    except TypeError:  # a savefig without matplotlib's keywords
        try:
            savefig(buf, format="png")
        except Exception:  # noqa: BLE001 - a figure that cannot draw is refused, not raised
            return None
    except Exception:  # noqa: BLE001 - a figure that cannot draw is refused, not raised
        return None
    return buf.getvalue()


def render_png(fig: Any) -> bytes | None:
    """The figure as the largest PNG that fits the cap; the smallest one when none does; None if it cannot draw.

    At each size it tries the full-colour PNG, then its palette copy, and only then goes smaller.
    """
    savefig = getattr(fig, "savefig", None)
    if savefig is None:
        return None
    data = b""
    for dpi in DPI_STEPS:
        data = _save_png(savefig, dpi)
        if data is None:
            return None
        if _fits(data):
            return data
        smaller = _palette_png(data)
        if smaller is not None and _fits(smaller):
            return smaller
    return data  # over the cap; the caller says so
