"""rakiura: log your progress on Rakiura Labs steps from a Jupyter notebook.

Four lines::

    import rakiura
    rakiura.start(notebook="onboarding/tutorial_1.ipynb")  # first time: open the link, click Allow
    rakiura.done("part-2")                                  # ticks that part on its step
    rakiura.done("exercise-1", figures=[fig])               # ticks it and adds the figure
    rakiura.submit()                                        # only when you choose: sends the step for review

Design rules:

* Connecting is one click. ``rakiura.login()`` (which ``start()`` calls)
  prints a link; open it while signed in to Rakiura, check the code matches,
  and click Allow. The key it receives is saved in your config directory
  (``~/.config/rakiura/credentials``, readable only by you), never in the
  notebook or its repository, and only ever sent back to the Rakiura address
  it came from. ``RAKIURA_KEY`` in the environment overrides it, and Colab's
  Secrets panel works too. A key never needs to be written in a notebook.
* Every tick and figure carries a run: the notebook, the repository and
  commit actually run, whether the checkout was edited, and where it ran
  (laptop, Colab, Binder). Forks and personal copies are labelled, never
  refused. Nothing else about your computer is sent: no host or user names,
  no absolute paths, no environment variables, no file contents.
* Which step a part counts toward is decided in Rakiura, so a notebook only
  carries part names like ``"part-2"``.
* Nothing is submitted for review unless you run ``rakiura.submit()``.
* Nothing here ever raises into the notebook. A missing key, a laptop that is
  offline, a revoked key or a server that says no all print ONE line and
  return ``None`` (or ``False``), so a tutorial keeps running.
* Pure standard library. ``requests`` is used when it is installed, otherwise
  ``urllib``; neither is required.
* The key only travels over https (plain http only to this computer, for
  testing), and never follows a redirect.

The cell-tag functions of 0.1 (``me``, ``checkpoint``, ``stage_figure`` ...)
still work.

Where things are: ``_login`` (connecting), ``_runs`` (start), ``_steps``
(done, submit), ``_artifacts`` (stage_*), ``_cells`` (the 0.1 cell tags),
``_transport`` (HTTP), ``_credentials`` (the saved key).
"""

from ._artifacts import stage_figure, stage_link, stage_text
from ._cells import checkpoint, checkpoint_cell, me, uncheck
from ._config import API_PREFIX as API_PREFIX
from ._config import DEFAULT_URL as DEFAULT_URL
from ._config import FILE_TEXT_MAX_BYTES as FILE_TEXT_MAX_BYTES
from ._config import KEY_PREFIX as KEY_PREFIX
from ._config import MAX_NUMBERS as MAX_NUMBERS
from ._login import login, logout
from ._runs import share_notebook, start
from ._session import RakiuraError
from ._session import session as _session  # noqa: F401 - the README points at rakiura._session.last_error
from ._steps import done, submit
from ._version import __version__ as __version__

__all__ = [
    "RakiuraError",
    "checkpoint",
    "checkpoint_cell",
    "done",
    "login",
    "logout",
    "me",
    "share_notebook",
    "stage_figure",
    "stage_link",
    "stage_text",
    "start",
    "submit",
    "uncheck",
]
