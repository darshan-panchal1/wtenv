# Implementation Plan: wtenv — Per-Worktree Runtime Isolation

**Branch**: `001-worktree-runtime-isolation` | **Date**: 2026-10-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-worktree-runtime-isolation/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

`wtenv` is a local command-line tool that gives each git worktree its own port block, env
file section, database, and docker-compose project, and removes them again safely.

Technical approach, in one paragraph: a pure-Python package (`src/wtenv/`) with a Typer
command line. One JSON registry in the per-user state directory records everything wtenv
allocates and creates, keyed by each worktree's git directory. A short registry lock guards
every read-check-write; a per-worktree lock serialises `up` and `down`. Ports are found by
binding test sockets and reserved in the registry under the lock. Postgres databases are
copied with `CREATE DATABASE … TEMPLATE` through psycopg; compose isolation is an
auto-loaded override file generated from Compose's own resolved model. Teardown reads only
the registry, and `gc` releases an entry only when git confirms the worktree is gone.

Supporting documents: [research.md](research.md) (evidence and decisions),
[data-model.md](data-model.md), [contracts/](contracts/) (command line, JSON documents,
`wtenv.toml`, file formats), [quickstart.md](quickstart.md) (runnable acceptance test).

## Technical Context

**Language/Version**: Python 3.12 for development; minimum supported 3.11 (`tomllib`,
`StrEnum`). Managed with uv.

**Primary Dependencies**: `typer` (CLI), `pydantic` v2 (config, registry, `--json` models),
`psycopg[binary]` 3 (Postgres, no ORM), `filelock` (locks), `platformdirs` (state
directory). Standard library `tomllib`, `socket`, `subprocess`, `json`. Docker through the
`docker compose` and `docker` programs as subprocesses; no Docker SDK. git through the `git`
program. Nothing beyond the maintainer's list; each is justified in research.md, section 7.

**Storage**: one JSON file, `registry.json`, in the per-user state directory
(`platformdirs.user_state_path("wtenv")`), written by temporary file and `os.replace`. Lock
files beside it. No database of its own.

**Testing**: `pytest` and `pytest-cov`. Marker `integration` for tests that need real git
worktrees or Docker. Postgres and compose tests use `testcontainers` and skip, with the
message "Docker is not available", when Docker is not running. `ruff` (lint and format) and
`mypy` with `strict = true` in `pyproject.toml`, so the gate `uv run mypy src` is strict.

**Target Platform**: macOS and Linux as first-class platforms; Windows only inside WSL2
(NFR-004). Needs git 2.31 or later. Compose isolation needs Docker Compose 2.24.4 or later;
Postgres isolation needs a local PostgreSQL 13 or later.

**Project Type**: command-line tool, single package, `src/` layout, `hatchling` build
backend, console script `wtenv`.

**Performance Goals**: `wtenv --version` and `wtenv ls --json` under 300 ms mean, warm,
measured with `hyperfine` (NFR-001). `up` under 5 s of wtenv's own work (NFR-002).

**Constraints**: no network except the configured local Postgres server and the local Docker
engine; no telemetry; no daemon; no prompts; only what the spec defines.

**Scale/Scope**: one developer machine, 2–5 worktrees at a time across a few repositories;
the port range holds 1000 blocks of the default size. Nine commands, 85 functional
requirements (FR-055 not delivered), six user stories.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Checked against constitution **v1.0.2**. Result: **pass, with no violations**.

The check was first made against v1.0.1, before research and again after the design in
data-model.md and contracts/. Both times it passed with one deviation, the SQLite side
files. Constitution v1.0.2 amended Principle II to cover them, so the deviation is resolved
and the design is unchanged. It is listed under Resolved deviations.

| Principle | Status | How the design meets it |
|-----------|--------|-------------------------|
| I. Local-First | Pass | Postgres host must be `localhost`, `127.0.0.1`, or `::1`, checked before connecting (FR-025). Docker endpoint must be local, checked before any engine call (FR-035). No other network code; no telemetry or update check. |
| II. Never Destroy User Data | Pass | Everything is recorded before it is created (resource states). Removal needs a registry record, never a name match. Compose resources are selected by the recorded project's label (v1.0.1). A recorded SQLite file's `-wal`, `-shm`, and `-journal` side files are removed with it; that record covers them (v1.0.2). `down`, `gc`, and `hook uninstall` have `--dry-run` and report every item. `up` never removes a database, container, or volume. Reading of "destructive command": regenerating or removing a file that wtenv itself generated and recorded (its old env section when `env_file` changed, its override file when the compose file moved or `[compose]` was removed) is part of `up` (and of `down`), reported in `changes`; it does not make `up` a destructive command that needs `--dry-run` (data-model.md, Resource states). |
| III. Deterministic & Idempotent | Pass | Port assignment and names are pure functions of the registry and configuration. Registry access is locked; writes are atomic. Interrupted work is resumed from recorded states. |
| IV. Agent-Native | Pass | `--json` on every command, including `--version`. One stable code and one exit status per error category. No prompts, no `--interactive` flag. |
| V. Zero-Config Default | Pass | Ports and env file work with no `wtenv.toml`. Database and compose are on only when their table is in `wtenv.toml`. |
| VI. Platforms | Pass | macOS and Linux; `flock` locks; CI runs both. WSL2 behaves as Linux. |
| VII. Test-First | Pass | Tasks put each core module's tests before its code. Unit tests for allocation and registry; integration tests on real worktrees; 80% line coverage per core area, measured per area; `mypy` strict; CI gates below. |
| VIII. Simplicity | Pass | wtenv's wheel is pure Python and installs with `uv tool install wtenv`. Five runtime dependencies, each justified. No daemon. Startup measured at about 194 ms on the slowest budgeted path; benchmark task planned. Note: `pydantic-core` and `psycopg-binary` are compiled wheels; no compiler is needed. |
| IX. Readability | Pass | Plain functions and pydantic models; one module per concern; no metaprogramming; docstrings and full type hints. |
| X. Small Surface | Pass | Exactly the commands of FR-001. Flags: `--json`, `--dry-run`, `gc --release`. No environment variables of its own. Ideas found during planning are in `docs/roadmap.md`. |
| Technical constraints | Pass | Python 3.12/3.11, uv, `src/` layout, ruff, mypy strict, pytest with the `integration` marker, real `git worktree add`, Docker tests skip cleanly. |
| Workflow and gates | Pass | Conventional Commits, one commit per task group, no attribution trailers, the five gate commands in CI. |

### Violations

None.

### Resolved deviations

1. **SQLite side files (Principle II): resolved by constitution v1.0.2.** On removing a
   worktree's SQLite database, wtenv also deletes that file's `-wal`, `-shm`, and
   `-journal` side files. SQLite created them, not wtenv, and they are not recorded one by
   one, so under v1.0.1 this departed from Principle II's first rule, read literally.
   Constitution v1.0.2 says that "created and recorded" also covers these side files next
   to a recorded SQLite database file, as v1.0.1 did for compose resources. The
   justification written for the deviation is kept in Complexity Tracking.

No other deviation was found.

## Project Structure

### Documentation (this feature)

```text
specs/001-worktree-runtime-isolation/
├── plan.md              # This file
├── research.md          # Phase 0: evidence and decisions
├── data-model.md        # Phase 1: registry, states, classification, allocation
├── quickstart.md        # Phase 1: walkthrough and runnable acceptance test
├── contracts/
│   ├── cli.md           # Commands, flags, exit statuses, error details
│   ├── json_models.py   # The --json documents, as pydantic models
│   ├── config.md        # wtenv.toml
│   └── files.md         # Env section, exclude block, hook block, override file, state dir
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks; not created by /speckit-plan)

docs/
└── roadmap.md           # Ideas outside the spec (Principle X)
```

### Source Code (repository root)

```text
pyproject.toml           # hatchling; console script wtenv = "wtenv.cli:main"; mypy strict; ruff; pytest
README.md
src/wtenv/
├── __init__.py          # __version__ (single source for the package version)
├── __main__.py          # python -m wtenv
├── cli.py               # Typer app: one function per command, lazy imports, exit-status mapping
├── errors.py            # ErrorCode, exit-status table, WtenvError (standard library only)
├── output.py            # Result models (same as contracts/json_models.py) and text rendering
├── gitutil.py           # Run git with a scrubbed environment; worktree list; tracked check; git paths
├── identity.py          # WorktreeIdentity; resolve the current worktree; points_to()
├── config.py            # wtenv.toml model, defaults, validation
├── registry.py          # Registry models; load, save, transaction
├── locks.py             # Registry lock and worktree lock, with their bounds
├── ports.py             # Free-port test, block search, port assignment
├── envfile.py           # Managed section: read, write, remove; value quoting
├── exclude.py           # Managed block in .git/info/exclude
├── database.py          # Names, URL pattern, Postgres and SQLite create and remove
├── compose.py           # Model resolution, override text, verification, project teardown
├── provision.py         # up: orchestration and post-up commands
├── teardown.py          # Release of one entry; shared by down and gc
├── orphans.py           # classify(), the orphan checks, gc orchestration
├── listing.py           # Views for ls
├── doctor.py            # Checks and findings
├── hooks.py             # Git hook install and uninstall
└── execcmd.py           # exec

tests/
├── conftest.py          # Isolated state directory (XDG_STATE_HOME), repository and worktree factory, Docker skip
├── fixtures/sample_app/ # Acceptance fixture: app.py, compose.yaml, wtenv.toml
├── unit/                # No git worktrees, no Docker
├── contract/            # Exit statuses and --json documents against contracts/json_models.py
└── integration/         # Marker "integration": real git worktrees; Postgres and compose through testcontainers

scripts/
└── bench-startup.sh     # The NFR-001 hyperfine procedure

docs/
├── roadmap.md
└── benchmarks.md        # NFR-001 results per release

.github/workflows/
├── ci.yml               # The five gates, on Linux and macOS, Python 3.11 and 3.12
└── release.yml          # v* tags: uv build, then uv publish with PyPI trusted publishing
```

**Structure Decision**: a single project with a `src/` layout. One module per concern, so
each NFR-003 core area is one or two files with its own coverage figure. `cli.py` is the only
module imported at start-up; every command imports what it needs when it runs.

## Decisions settled in this plan

The spec left these to planning. Each is now fixed; the contracts are the authority.

| Item | Decision | Where |
|------|----------|-------|
| Registry lock wait bound (FR-078) | 10 seconds, then `registry_busy` | research §9 |
| Worktree lock wait bound (FR-078) | 60 seconds for `up` and `down`, then `worktree_busy`; `gc` does not wait | research §9 |
| `gc` option for explicit release (FR-073) | `--release PATH`, repeatable; acts only on the named entries | contracts/cli.md |
| Error codes and exit statuses (FR-059) | 19 codes, statuses 1–19; `exec` uses 125, 126, 127 | contracts/cli.md, json_models.py |
| `doctor` finding codes (FR-061) | Nine codes; severity `problem` or `info` | contracts/cli.md |
| `.git/info/exclude` on `down` (FR-085) | Block removed only with the last registered worktree of the repository | contracts/files.md |
| Port range (FR-010) | 20000–29999; 1000 blocks of size 10 | research §11 |
| Worktree identity (FR-006) | `realpath` of `git rev-parse --absolute-git-dir` | research §6, data-model.md |
| Tying a compose port to a variable (FR-031) | `${VAR}` in the compose file; detected with marker values | data-model.md |
| Compose mechanism (FR-030) | Auto-loaded override file with `name:` and `ports: !override` | research §4, contracts/files.md |
| Template in use (FR-082) | Session count checked before `CREATE DATABASE`; `55006` also mapped | research §3 |
| Claude Code integration (FR-055) | Not delivered in v1 | research §2 |
| Minimum versions | git 2.31, Docker Compose 2.24.4, PostgreSQL 13, Python 3.11 | research §3, §4, §6 |

## Design overview

Full detail is in data-model.md and the contracts; this is the map.

**Start-up and errors.** `wtenv.cli:main` runs the Typer app with `standalone_mode=False`,
so wtenv alone decides every exit status. A `WtenvError` carries an `ErrorCode`; usage errors
from Typer become `usage_error`; anything else becomes `internal_error`. With `--json`, the
command's result model is printed even on failure. Typer 0.27 ships its own copy of click;
wtenv never imports `click`.

**`up`** (provision.py): identify → worktree lock → all checks that need no resource →
registry transaction (entry, block, port assignment, exclude block) → database → compose
override and verification → env section → post-up commands → mark `provisioned`. Order and
failure behaviour are in contracts/cli.md.

**`down`** (teardown.py): worktree lock → compose project → databases → env section →
registry entry, block, and (for the last worktree of a repository) exclude block. Items that
cannot be removed stay recorded.

**`gc`** (orphans.py): snapshot → `classify` every entry → for each orphaned entry, take its
lock without waiting, classify again, release through teardown.py. `--release PATH` handles
only the named entries, after checking that none of them is a live worktree.

**`ls`, `doctor`, `exec`**: read a registry snapshot under the registry lock and use the
same `classify`. They take no worktree lock.

**Recovery.** Every resource is recorded with state `creating` before it exists and
`removing` before it is removed (data-model.md, "Resource states"). There is no moment at
which a resource wtenv created is unrecorded (FR-067).

## Coverage map (NFR-003)

Line coverage of at least 80%, per area, not averaged, from the CI job where Docker is
available so that nothing is skipped.

| # | Core area | Modules |
|---|-----------|---------|
| 1 | Registry | `registry.py`, `locks.py` |
| 2 | Port allocation | `ports.py` |
| 3 | Worktree identity | `identity.py` |
| 4 | Config parsing and validation | `config.py` |
| 5 | Database provisioning | `database.py` |
| 6 | Compose override generation | `compose.py` |
| 7 | Garbage collection | `orphans.py` |
| 8 | Env-file writing | `envfile.py` |

CI runs the whole suite once with `--cov=wtenv`, then one
`uv run coverage report --fail-under=80 --include=<modules of the area>` per row.

## Test strategy

- **Unit** (`-m "not integration"`): ports (search, overlap, assignment, markers), config
  (every rule in contracts/config.md), registry (round trip, atomic write, unknown version,
  damaged file), locks (bounds, release when the holder is killed), env file (append,
  rewrite in place, damaged markers, quoting, byte-for-byte preservation), exclude block,
  override text (golden files), `classify` (every row of the classification), names, result
  models.
- **Contract**: every command's `--json` output validates against
  `contracts/json_models.py`; every error code produces its exit status.
- **Integration** (`-m integration`): one file per user story, using real `git worktree add`
  in temporary directories; concurrency (SC-005: five `up` runs at once, twenty trials);
  recovery (kill `up` and `down` at each step, then run again); safety decoys (SC-007).
- **Acceptance**: quickstart.md, run before each release.
- Test-first: for each core area, the task list puts the failing tests before the code
  (Principle VII).

## Performance plan (NFR-001, NFR-002)

- Lazy imports: `cli.py` imports only `typer` and the standard library. `psycopg` is
  imported by the database step only; `compose.py` only when compose is configured; pydantic
  models by the commands that read the registry or print JSON.
- Measured prototype of the `ls --json` path: 194 ms mean (research §8).
- Task: `scripts/bench-startup.sh` runs the two `hyperfine` commands of NFR-001; the result
  goes to `docs/benchmarks.md` with machine model, OS version, and wtenv version, and is
  repeated before each release.
- NFR-002: an integration test times a first `up` and a repeat `up` on the sample app and
  fails above 5 seconds.

## Build, CI, and release

- `pyproject.toml`: `requires-python = ">=3.11"`; dependencies `typer>=0.27,<1`,
  `pydantic>=2.13,<3`, `psycopg[binary]>=3.3,<4`, `filelock>=4.0,<5`,
  `platformdirs>=4.12,<5`; dev group `pytest`, `pytest-cov`, `ruff`, `mypy`,
  `testcontainers`; `[tool.mypy] strict = true`, `python_version = "3.11"`,
  `plugins = ["pydantic.mypy"]`; `[tool.ruff] line-length = 100`,
  `target-version = "py311"`; version read from `src/wtenv/__init__.py`.
- CI (`ci.yml`): Linux and macOS, Python 3.11 and 3.12, the five gate commands of the
  constitution. The Linux job has Docker and enforces the coverage map.
- Release (`release.yml`): on `v*` tags, a `build` job (`uv build`) and a separate `publish`
  job (`uv publish`, `id-token: write`, environment `pypi`) using PyPI trusted publishing.
  The first release uses a "pending" publisher. The name `wtenv` was free on 2026-10-03
  (research §5).
- Install: `uv tool install wtenv` or `pipx install wtenv`.

## Delivery order for `/speckit-tasks`

| Phase | Content | Delivers |
|-------|---------|----------|
| Setup | `pyproject.toml`, package skeleton, CI workflow, ruff and mypy settings | Gates run |
| Foundation | `errors`, `output`, `gitutil`, `identity`, `registry`, `locks`, `cli` shell, `--version` | Everything below builds on it |
| US1 (P1) | `config` (ports, block size, env file), `ports`, `envfile`, `exclude`, `provision`, `up` | Port and env isolation with no configuration |
| US2 (P2) | `database` (Postgres, SQLite), URL pattern, post-up commands | Database isolation |
| US3 (P3) | `compose`: resolution, override, verification | Compose isolation |
| US4 (P4) | `teardown`, `down`, `orphans`, `gc`, `listing`, `ls` | Lifecycle and cleanup |
| US5 (P5) | `hooks`, `execcmd` | Auto-provisioning and `exec` |
| US6 (P6) | `doctor`; contract tests for every command | Diagnostics and the agent contract |
| Polish | Benchmark, quickstart run, README, release workflow | Release |

Post-up commands (FR-036, FR-037) belong to no user story in the spec; they are delivered
with US2, where migrations are their first use. User Story 5 scenario 4 has no tasks.

## Spec changes made during planning

All are in spec.md, under "Decided during planning" in the Clarifications section.

| Change | Reason |
|--------|--------|
| FR-006 rewritten; FR-084 added; "moved" edge case split in two; assumption renamed "Identity follows the git directory" | Maintainer decision: the git directory is the identity, so a moved linked worktree keeps its resources |
| FR-073 extended: `gc --release` also refuses an entry whose worktree was moved and still exists | Follows from the above: releasing it would drop a live worktree's database |
| New edge case: a worktree created again under the same directory name takes over the old entry | Consequence of git reusing git directories (research §6) |
| FR-055 and US5 scenario 4 marked not delivered; assumptions and Key Entities updated; new edge case for tools that switch hooks off | Maintainer decision under the "Claude Code hooks" assumption (research §2) |
| FR-085 added | Maintainer decision on `.git/info/exclude` |

**Points in the spec this plan reads in a particular way** (for `/speckit-analyze`):

- FR-020 calls `DATABASE_URL` the "default" variable, but FR-063's closed list has no setting
  to change it. v1 always uses `DATABASE_URL`; a setting is on the roadmap.
- FR-029 says "every host-published port". A port with no fixed host port (`"3000"`) is
  therefore pinned to a block port as well.
- "Provisioned" means `up` completed. A failed post-up command leaves the worktree
  `incomplete`, and `exec` refuses it until `up` succeeds (FR-049, FR-057).
- FR-018 forbids modifying tracked files, so an env file that git tracks is refused with
  `env_file_unusable`.
- FR-059 asks for a stable exit status per failure. `exec` reports wtenv's own failures as
  125, with the specific code in the error; the reason is in research §10.

## Risks

| Risk | Mitigation |
|------|------------|
| `wtenv` is taken on PyPI before the first upload | Publish early once US1 works, or fall back to a name from research §5 |
| Start-up budget: about 100 ms of headroom, 60 ms of it is `filelock`'s import | Benchmark task; contingency on the roadmap |
| Compose minimum (2.24.4) and git minimum (2.31) come from documentation; only Compose 5.1.4 and git 2.54 were run | Version checks fail with `dependency_unavailable`; CI can add an older-version job later |
| Claude Code's behaviour (hooks off for its git calls) is observed, not documented, and may change | Nothing in v1 depends on it; if hooks run again, the git hook simply starts provisioning those worktrees |
| A re-created worktree takes over the old entry and database | Documented edge case; `gc` first, or `down` then `up`, gives a fresh database |

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No open violations. The row below is the justification written for the SQLite side-file
deviation, which constitution v1.0.2 resolved (see Constitution Check). It is kept as the
reason for that amendment.

| Resolved deviation | Why Needed | Simpler Alternative Rejected Because |
|--------------------|------------|-------------------------------------|
| Principle II as it stood in v1.0.1: deleting a SQLite database's `-wal`, `-shm`, and `-journal` files, which SQLite created and the registry does not list one by one | They are part of the database's stored state (sqlite.org/wal.html). A journal left beside a later fresh copy is paired with the wrong database, which SQLite documents as a cause of corruption (sqlite.org/howtocorrupt.html, section 1.4) | Deleting only the recorded file leaves stale side files that can corrupt the next copy. Recording them at creation is not possible, because SQLite creates them later, while the app runs |
