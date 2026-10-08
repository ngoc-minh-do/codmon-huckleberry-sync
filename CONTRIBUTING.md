# Contributing

Thanks for taking a look. This is a small, unofficial integration project, so
keeping changes focused and well-tested matters more than process ceremony.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.14 (pinned in
`.python-version`).

```bash
uv sync
cp .env.example .env   # fill in credentials to run against the real services
```

Install the git hooks (ruff, uv-lock, ty, commitizen) once:

```bash
make install   # or: uv sync && uv run prek install
```

## Canonical check

Run exactly what CI runs before opening a pull request:

```bash
make check
```

That runs `ruff check`, `ruff format --check`, `ty check`, and `pytest`.
Formatting is enforced with `ruff format`; apply it with `make fix` (or
`uv run ruff format .`). Type checking uses [ty](https://github.com/astral-sh/ty),
run with `--exit-zero-on-warning` while adoption is incremental.

## Tests

Tests live in `tests/` and must not touch the network or real accounts. New
parsing/mapping behavior should come with a focused unit test.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`. A PR
whose commits do not validate is rejected by the `commit-lint` workflow.

## Pull requests

- Branch off `main`; keep PRs small and focused.
- Describe the motivation and the behavior change, and note any manual
  verification you performed.
- Ensure the canonical check passes and the changelog is updated under
  `## [Unreleased]` for user-visible changes.
- PRs are squash-merged so `main` stays linear.

## Scope and risk

Both Codmon and Huckleberry integrations are reverse-engineered and may break
without notice. Changes that write to Huckleberry must default to the existing
dry-run behavior and preserve idempotency (see the dedupe logic in
`src/codmon_sync/hb/writer.py`).
