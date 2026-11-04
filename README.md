# rakiura

The Rakiura notebook helper. A cell in your Jupyter or Colab notebook can tick an item on your
Rakiura checklist, add a figure or a few numbers to a step, and send the step for review when you
choose. The step page in Rakiura always works without it.

Pure standard library, Python 3.11 or later (tested on 3.11 to 3.15; Colab runs 3.12). It uses
`requests` when that is installed, and never needs it.

It is a Python package, not a notebook you copy, because every lab's notebooks install it with one
`pip install` line (below) and call the same few functions.

## Install

In a notebook cell (Colab, Jupyter, VS Code):

```python
%pip install -q "rakiura @ git+https://github.com/Rakiura-Inc/rakiura-py@main"
```

In a terminal:

```sh
pip install "rakiura @ git+https://github.com/Rakiura-Inc/rakiura-py@main"
```

## Quickstart

This is the same quickstart as in Rakiura under Settings > Notebook key.

```python
import rakiura

rakiura.start(notebook="path/to/notebook.ipynb")  # the first time, open the link it prints and click Allow
rakiura.done("part-2")  # after you finish a part: ticks it on its step
rakiura.done("exercise-1", figures=[fig])  # ticks it and adds the figure to the step
rakiura.submit()  # when you're ready, sends the step for review
```

The first time, `rakiura.start()` prints a link like
`https://labs.rakiura.co/oauth/device?code=WXYZ-BCDF`. Open it while signed in to Rakiura, check
that the code matches the one in your notebook, pick your lab and click **Allow**. The cell
finishes on its own.

`notebook=` is the notebook's path inside its repository. In Colab, where there is no git
checkout, add the repository too: `rakiura.start(notebook="...", repo="owner/name")`.

## Colab

Colab throws the machine away after each session, so you click Allow again in each new session.
Binder works the same way. To skip the click, add a secret named `RAKIURA_KEY` in Colab's
Secrets panel with a key from Settings > Notebook key.

## Offline and errors

No call ever raises an error into your notebook. If you are offline, not signed in, your key was
revoked, a part is not linked to a step yet, or a step cannot take a submission, the call prints
one line starting with `rakiura:`, returns `False` (or `None`), and the notebook carries on.
Your work is never lost: tick the box on the step page instead. The last message is in
`rakiura._session.last_error`.

## Your key

- After you click Allow, the key is saved in your config folder, `~/.config/rakiura/credentials`
  (`$XDG_CONFIG_HOME` is respected; `%APPDATA%` on Windows), readable only by you. It is never
  saved in the notebook or inside a git repository. Set `RAKIURA_CREDENTIALS` to use another path.
- The key is sent only to the Rakiura address it came from, only over https, and never printed.
- It lasts 30 days. Revoke it any time in Rakiura under Settings > Notebook key.
  `rakiura.logout()` forgets it on this computer.
- `RAKIURA_KEY` in the environment overrides everything, for a terminal or a job. A key typed into
  a notebook (`rakiura.login("rknb_...")`) works but prints a warning, because notebooks get shared.

## What a run sends

`rakiura.start()` sends Rakiura only this:

- the notebook path inside its repository (outside a repository, the file name only)
- the repository, as `owner/name` from `git remote get-url origin` (any credentials in the URL are dropped)
- the commit (`git rev-parse HEAD`)
- whether files other than the notebook were edited
- where it runs: `local`, `colab` or `binder`
- this helper's version and Python's

Never host names, user names, absolute paths, environment variables or file contents. The reviewer
sees, for example, "Tutorial 1, fork at 8f7378c (edited), Colab".

## Parts and steps

A notebook only names its parts (`"part-2"`, `"exercise-1"`). Your lab's PI decides in Rakiura
which step each part counts toward (Settings > Notebook links), so the notebook carries no Rakiura
ids. A part that is not linked yet prints one line and nothing is logged.

To keep a notebook running for people without the helper:

```python
try:
    import rakiura
except ImportError:
    rakiura = None
if rakiura:
    rakiura.start(notebook="tutorials/tutorial_1.ipynb", repo="owner/name")
...
if rakiura:
    rakiura.done("part-2")
```

## Submitting

`rakiura.submit()` sends the step of your last `done(...)` for review, with everything you added
to it from the notebook. `submit(step="ONB-001-D1")` names a step instead, and `note="..."` adds a
note. It goes through the same checks as the Submit button on the step page. Nothing is submitted
unless you run `submit()` or `done(..., submit=True)`. A key can submit at most 10 times a day.
When your commit is pushed to GitHub and unedited, a link to the notebook is attached too
(`rakiura.share_notebook()` prints it).

## Figures, numbers and files

- `figures=` takes the matplotlib figures you drew on (`fig, ax = plt.subplots()`). After
  `plt.show()`, `plt.gcf()` is a new, empty figure, so the helper refuses it.
- Each figure must fit in 50 KB. The helper tries 100 dpi first, then a smaller palette PNG
  (with Pillow, which ships with matplotlib), and only then a lower resolution.
- `numbers=` takes up to 20 named single values: numbers, short text, True or False. Never a
  table, an array or a DataFrame.
- `rakiura.stage_text(text, title, step="ONB-001-D1")` adds a small text or CSV file (up to 50 KB)
  to a step. `stage_link(url, title, step=...)` adds a link.
- `rakiura.me()` lists the steps you can log against, with each item's cell tag
  (`STEP-ID/item-key`) for `rakiura.checkpoint(tag)`.

## Settings

- `RAKIURA_URL` points the helper at another Rakiura address. It must be https (plain http only
  to this computer, for testing).
- `RAKIURA_NO_BROWSER=1` stops it opening the sign-in link in your browser.

## Development

You need [uv](https://docs.astral.sh/uv/) and [prek](https://prek.j178.dev/) (or pre-commit).

```sh
uv sync                  # a .venv with the package (editable) and the dev tools
uv run pytest            # the tests, in parallel and in random order
uv run ruff check .      # lint; `uv run ruff format .` formats
uv run ty check          # type check
prek install             # run the git hooks on every commit (prek run --all-files to run them now)
```

The tests run a small fake server on this computer, or mock the HTTP layer. No network, no real
key, and never your real config folder. CI runs them on Python 3.11 to 3.15, then installs the
built wheel in a clean venv and runs them again against it. CI also runs the git hooks,
pip-audit (on every change and weekly) and OpenSSF Scorecard, all through
[Rakiura-Inc's shared workflows](https://github.com/Rakiura-Inc/.github). See
[CONTRIBUTING.md](CONTRIBUTING.md) for how to send a change, and [SECURITY.md](SECURITY.md) to
report a vulnerability privately.

This repository follows [Rakiura-Inc/python-template](https://github.com/Rakiura-Inc/python-template)
(the `library` flavour). `uvx copier update --trust` pulls in later template fixes.

## Releasing

1. Bump `__version__` in `src/rakiura/_version.py` and add the entry to `CHANGELOG.md`.
2. Merge to `main`, then tag that commit and push the tag: `git tag v0.3.0 && git push origin v0.3.0`.
3. The Release workflow builds the wheel and sdist, records their build provenance and attaches
   them to a GitHub Release. The shared release workflow signs the provenance, so check a
   download with
   `gh attestation verify <file> --repo Rakiura-Inc/rakiura-py --signer-workflow Rakiura-Inc/.github/.github/workflows/release.yml`.

Notebooks that install from `@main` get the change as soon as it is merged. To pin a notebook to a
release, install `@v0.3.0` instead of `@main`.

## Licence

MIT, copyright 2026 Rakiura Inc. See [LICENSE](LICENSE).
