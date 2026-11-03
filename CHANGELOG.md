# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

Nothing changes for a notebook.

### Changed

- CI calls Rakiura-Inc's shared workflows (tests, git hooks, release) instead of carrying its own
  steps. A release now also records build provenance for the wheel and sdist.
- Lint now limits function complexity (ruff C901 and PLR rules), as in the Rakiura template.

### Added

- pip-audit and OpenSSF Scorecard workflows, SECURITY.md (private vulnerability reports),
  CONTRIBUTING.md, CODEOWNERS and `.copier-answers.yml` for template updates.

## 0.3.1 - 2026-11-03

### Fixed

- Lab helpers again find a key saved in Colab Secrets or a credentials file. `rakiura._colab_secret()`
  and `rakiura._credentials_path()`, which nmrtrain and habtrain call and 0.3.0 removed, are back
  with their 0.2.0 behaviour, and a contract test keeps them from being removed again.

## 0.3.0 - 2026-11-03

Nothing changes for a notebook: `rakiura.start`, `done`, `stage_text`, `submit`, the one-click
Allow sign-in and the `RAKIURA_KEY` secret work exactly as before, with the same messages.

### Changed

- Needs Python 3.11 or later; tested on 3.11, 3.12, 3.13, 3.14 and 3.15. Python 3.9 and 3.10
  have reached end of life and are no longer supported. Colab runs 3.12.
- The code is split into small modules (sign-in, runs, steps, staging, HTTP, saved key) instead
  of one 1,100-line file. The public names are unchanged and still import from `rakiura`.
- Built with hatchling instead of setuptools; developed with uv.
- A credentials file with an expiry time that has no time zone no longer raises; the key is used.

### Added

- CI on every pull request: tests on Python 3.11 to 3.15 (in parallel and in random order), the
  built wheel installed in a clean venv and tested, ruff, ty, and the git hooks (pre-commit).
- A release workflow: pushing a `v*` tag builds the wheel and sdist and attaches them to a GitHub
  Release.
- Renovate configuration for dependency updates.
- More tests, including unit tests with the HTTP layer mocked.

## 0.2.0 - 2026-10-18

- One-click sign-in (`rakiura.start()` prints a link; click Allow), runs with notebook, repository
  and commit, `done()` and `submit()`.
