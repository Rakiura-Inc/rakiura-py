# Contributing

Thank you for helping. Changes come in as pull requests to `main`; CI must pass and a code owner
(see `.github/CODEOWNERS`) reviews each one.

## Set up

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs the right
Python and every dependency.

```sh
git clone https://github.com/Rakiura-Inc/rakiura-py.git
cd rakiura-py
uv sync                  # a .venv with the package (editable) and the dev tools
uvx prek install         # run the git hooks on every commit (or: uvx pre-commit install)
```

## Check your change

```sh
uv run pytest            # the tests, in parallel and in random order
uv run ruff check .      # lint; `uv run ruff format .` formats
uv run ty check          # type check
uvx prek run --all-files # every git hook, as CI runs them
```

## Style

- Code people learn from follows [the teaching code style](docs/teaching-code-style.md).
- Small functions with a docstring that says what they do; named constants instead of bare numbers;
  data in data files, not in code. Ruff enforces a complexity limit (C901, PLR); split a function
  rather than silencing it, and give every `noqa` or ignore a one-line reason.
- Tests must pass in any order and in parallel.

## Security

Never report a vulnerability in an issue or a pull request. See [SECURITY.md](SECURITY.md).
