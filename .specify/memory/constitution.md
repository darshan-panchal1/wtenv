# wtenv Constitution

## Core Principles

### I. Local-First

- wtenv MUST NOT make network calls, except to localhost services that the user has
  explicitly configured (for example a local Postgres server or the local Docker daemon).
- wtenv MUST NOT collect or send telemetry, analytics, crash reports, or update checks.

Rationale: wtenv runs inside developer machines next to source code and credentials, and is
driven by unattended coding agents. It has to be safe to run without anyone watching.

### II. Never Destroy User Data

- wtenv MUST only drop or delete databases, containers, volumes, or files that it created
  AND recorded in its registry. Both conditions are required; a matching name is not enough.
- Every destructive command MUST support `--dry-run`, which performs no changes and lists
  what would be touched.
- Every destructive command MUST report exactly what it touched.

Rationale: a tool that tears down environments automatically is only trustworthy if its
blast radius is provably limited to things it made itself.

### III. Deterministic & Idempotent

- Running `up` twice for the same worktree MUST yield identical ports, database, and env
  file contents.
- The registry is the single source of truth for what wtenv has allocated and created.
- Registry access MUST be file-locked, and registry writes MUST be atomic.
- A crash in the middle of any operation MUST leave state that wtenv can recover from.

Rationale: several agents run wtenv concurrently and may be killed at any moment. Results
must not depend on timing, ordering, or how many times a command was retried.

### IV. Agent-Native

- Every command MUST support `--json` for machine-readable output.
- Exit codes and error codes MUST be stable; once published, their meaning does not change
  without a constitution-compliant, documented breaking change.
- wtenv MUST NOT prompt interactively unless `--interactive` is passed.

Rationale: the primary callers are coding agents and scripts. A prompt that waits on stdin
or an error that can only be understood by reading prose blocks them.

### V. Zero-Config Default

- Port isolation and env-file isolation MUST work with no configuration file present.
- Postgres and docker-compose features are opt-in, and are enabled only through a
  `wtenv.toml` committed to the repository.

Rationale: the common case should cost nothing to adopt, and anything that creates heavier
resources should be an explicit, reviewable decision shared by the whole team.

### VI. Platforms

- macOS and Linux are first-class platforms and MUST be fully supported and tested.
- In v1, Windows is supported only through WSL2. Native Windows support is out of scope.

Rationale: a small, well-tested platform matrix beats a wide, partially working one.

### VII. Test-First

- Core logic MUST be developed test-first: the test is written and seen to fail before the
  implementation is written.
- Allocation and registry logic MUST have unit tests.
- Worktree behaviour MUST be covered by integration tests that use real git worktrees.
- Core modules MUST maintain at least 80% test coverage.
- `mypy --strict` MUST pass cleanly on `src/`.
- CI MUST be green before any merge.

Rationale: allocation and teardown bugs destroy work or cause silent collisions between
agents. They need to be caught by tests, not by users.

### VIII. Simplicity

- wtenv MUST be a pure-Python package installable with a single command
  (`uv tool install wtenv` or `pipx install wtenv`).
- Dependencies MUST be kept minimal; each new dependency needs a stated justification.
- v1 MUST NOT include a daemon or any long-running background process.
- CLI startup time MUST be under 300 ms.

Rationale: a tool that agents call many times per session has to be cheap to install,
cheap to start, and free of background state to go wrong.

### IX. Readability

- Code MUST be reviewable by an intermediate Python developer.
- Plain functions and dataclasses or pydantic models MUST be preferred over clever
  abstractions. Metaprogramming is not allowed.
- Public functions MUST have docstrings, and all code MUST carry full type hints.

Rationale: the maintainer reviews every diff. Code that cannot be understood on a first
read cannot be reviewed safely.

### X. Small Surface

- v1 MUST ship only what the spec defines.
- Any idea, feature, or option not in the spec MUST be recorded in `docs/roadmap.md`
  instead of being implemented.

Rationale: every extra command and flag is permanent surface area that has to be tested,
documented, and kept stable for agents.

## Technical Constraints

- Language: Python 3.12 for development; minimum supported version is Python 3.11.
- Tooling: dependencies and environments are managed with uv; the package uses a `src/`
  layout.
- Linting and formatting: ruff (`ruff check`, `ruff format`).
- Type checking: mypy in strict mode on `src/`.
- Testing: pytest, with integration tests selected by the `integration` marker.
- Integration tests MUST use real `git worktree add` in temporary directories.
- Tests that need Docker or Postgres MUST skip automatically, with a clear message, when
  Docker is unavailable.

## Development Workflow & Quality Gates

- The project follows Spec Kit spec-driven development. The source of truth is this
  constitution together with `specs/<feature>/{spec,plan,tasks}.md`.
- Nothing is implemented unless it is traceable to a task ID. If a task is ambiguous, work
  stops and the question is raised with the maintainer.
- Commits follow Conventional Commits, with one commit per completed task group.
- Commits and pull requests MUST NOT carry `Co-Authored-By`, "Generated with", or
  session-link trailers.
- Force-pushing is not allowed. `.env*` files and secrets MUST NOT be committed
  (`.env.example` is the exception).
- All of the following MUST pass before merge:
  - `uv run pytest -m "not integration"`
  - `uv run pytest -m integration`
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy src`

## Governance

- This constitution takes precedence over other project practices and documents. Where a
  spec, plan, or task conflicts with it, the constitution wins and the conflicting artifact
  is corrected.
- Any change to this constitution requires both a version bump and a rationale note. The
  rationale note is added to the Amendment Log below in the same change.
- Versioning follows semantic versioning:
  - MAJOR: a principle is removed or redefined in a backward-incompatible way.
  - MINOR: a principle or section is added, or guidance is materially expanded.
  - PATCH: clarifications, wording, and other non-semantic refinements.
- Every plan and every review MUST check compliance with these principles. A deviation is
  allowed only if it is written down with its justification in the feature's plan.
- `CLAUDE.md` holds the day-to-day development guidance and MUST stay consistent with this
  document.

### Amendment Log

| Version | Date | Rationale |
|---------|------|-----------|
| 1.0.0 | 2026-10-03 | Initial ratification of the ten core principles and governance rules. |

**Version**: 1.0.0 | **Ratified**: 2026-10-03 | **Last Amended**: 2026-10-03
