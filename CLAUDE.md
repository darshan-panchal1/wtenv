# wtenv

## Project

`wtenv` is a local CLI that gives each git worktree its own runtime: a unique
port block, its own Postgres/SQLite database, its own docker-compose project,
and its own env file, plus teardown and garbage collection.

Users: developers running 2–5 coding agents (Claude Code, Codex, Cursor) in
parallel worktrees.

## Workflow

Spec Kit spec-driven development (SDD).

- Source of truth: `.specify/memory/constitution.md` and
  `specs/<feature>/{spec,plan,tasks}.md`.
- Never implement anything that is not traceable to a task ID in `tasks.md`.
- If a task is ambiguous, stop and ask. Do not guess.

## Language

- Python 3.12 (minimum supported: 3.11), managed with uv.
- `src/` layout.
- Full type hints on all code.

## Commands

```sh
uv sync                              # install dependencies
uv run pytest -m "not integration"   # unit tests
uv run pytest -m integration         # integration tests
uv run ruff check .                  # lint
uv run ruff format --check .         # format check
uv run mypy src                      # type check
```

## Code style

- Readable over clever. The maintainer is a Python dev who reviews every diff.
- Docstrings on public functions.
- No metaprogramming.

## Git rules

- Conventional Commits.
- One commit per completed task group.
- NEVER add `Co-Authored-By`, "Generated with", or session-link trailers.
- Never force-push.
- Never commit `.env*` files or secrets (`.env.example` is fine).

## Testing rules

- Test-first for core logic.
- Integration tests use real `git worktree add` in temp dirs.
- Docker/Postgres tests auto-skip with a clear message when Docker is
  unavailable.
