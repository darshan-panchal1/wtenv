---
description: "Task list for wtenv per-worktree runtime isolation"
---

# Tasks: wtenv — Per-Worktree Runtime Isolation

**Input**: Design documents from `/specs/001-worktree-runtime-isolation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (cli.md,
config.md, files.md, json_models.py), quickstart.md, constitution v1.0.2

**Tests**: included and written first. Constitution Principle VII and the maintainer's
request require tests before implementation for the eight core areas of NFR-003: registry,
port allocation, worktree identity, config parsing, env-file writing, database provisioning,
compose override generation, and garbage collection.

**Organization**: one phase per user story, in priority order. Inside a phase, tasks are in
groups (`###`). Each group writes its tests first, sees them fail, then implements until they
pass. Phase order follows plan.md, "Delivery order for `/speckit-tasks`".

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run at the same time as the [P] tasks next to it: different files, and no
  dependency on an unfinished task. Whole groups can also run side by side; see
  "Dependencies & Execution Order".
- **[Story]**: US1–US6, the user stories of spec.md. Setup, Foundational, and Polish tasks
  have no story label.
- Every task line names the files it touches. Indented lines under a task belong to it: the
  cases to cover, and rules quoted from the design documents.
- Short names: `cli.md`, `config.md`, `files.md`, and `json_models.py` are in
  `specs/001-worktree-runtime-isolation/contracts/`; `data-model.md`, `research.md`, and
  `quickstart.md` are in the feature directory.

## Path Conventions

Single project with a `src/` layout (plan.md, Project Structure): `src/wtenv/`,
`tests/unit/` (no git worktrees, no Docker), `tests/contract/` (a module that creates git
worktrees or needs Docker carries `pytestmark = pytest.mark.integration`),
`tests/integration/` (marker `integration`), `tests/fixtures/`, `scripts/`, `docs/`,
`.github/workflows/`.

## Working rules

- **No merge to `main` before CI**: no branch, phase, or MVP merges to `main` until T141 (the
  CI workflow) is done and green on that branch (constitution, Principles VI and VII).
- **Test-first**: run a group's new tests and see them fail for the expected reason before
  writing the code that makes them pass.
- **One commit per task group**: commit a `###` group when its tests pass and the five gates
  pass. Conventional Commits. No `Co-Authored-By`, "Generated with", or session-link trailers.
- **The five gates**: `uv run pytest -m "not integration"`, `uv run pytest -m integration`,
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- **Code**: full type hints, docstrings on public functions, plain functions and pydantic
  models, no metaprogramming (Principle IX). `cli.py` imports implementations inside each
  command function (NFR-001).
- **Scope**: build only what a task says. An idea outside the spec goes to
  `docs/roadmap.md` (Principle X). If a task is ambiguous, stop and ask.
- **Readings R1–R9** (end of this file) are choices the design documents left open. The
  maintainer has confirmed them, and they are recorded in the contracts. The tasks that
  depend on one say so.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: an installable, empty `wtenv` package on which all five gates pass.

- [X] T001 Create `pyproject.toml` (build, metadata, dependencies, console script) and `.python-version`
  - `[build-system]` hatchling. `[project]`: `name = "wtenv"`, `requires-python = ">=3.11"`,
    `dynamic = ["version"]` with `[tool.hatch.version] path = "src/wtenv/__init__.py"`.
  - Runtime dependencies, exactly these five (plan.md, Build): `typer>=0.27,<1`,
    `pydantic>=2.13,<3`, `psycopg[binary]>=3.3,<4`, `filelock>=4.0,<5`,
    `platformdirs>=4.12,<5`.
  - `[dependency-groups] dev`: `pytest`, `pytest-cov`, `ruff`, `mypy`, `testcontainers`.
  - `[project.scripts] wtenv = "wtenv.cli:main"`. `.python-version` holds `3.12`.
  - No `readme` key yet; T145 adds it with the README.
- [X] T002 Add tool settings to `pyproject.toml`: mypy, ruff, pytest, coverage
  - `[tool.mypy]`: `strict = true`, `python_version = "3.11"`, `plugins = ["pydantic.mypy"]`.
  - `[tool.ruff]`: `line-length = 100`, `target-version = "py311"`.
  - `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `addopts = "--strict-markers"`,
    `markers = ["integration: needs real git worktrees or Docker"]`.
  - `[tool.coverage.run]`: `source = ["wtenv"]`, `patch = ["subprocess", "execv"]`, so that
    `wtenv` processes started by tests are measured (pytest-cov 7 no longer does this; the
    `subprocess` patch turns on `parallel`).
- [X] T003 [P] Create `src/wtenv/__init__.py` with a module docstring and `__version__ = "0.1.0"`, the single source of the version
- [X] T004 [P] Create shared test fixtures in `tests/conftest.py`
  - `state_home` (autouse): `XDG_STATE_HOME` points at a temporary directory for the test
    and every process it starts, so no test touches the developer's registry (research.md §7).
  - Git setup for every test: fixed author and committer identity; `GIT_CONFIG_GLOBAL` and
    `GIT_CONFIG_SYSTEM` set to `os.devnull`, so the developer's own git config (for example
    `core.hooksPath`) cannot change results.
  - `make_repo(name)`: `git init -b main` plus one empty commit; returns the resolved path.
    `add_worktree(repo, name, branch)`: the real `git worktree add` (constitution, Technical
    Constraints).
  - `run_wtenv(args, cwd, env=None)`: runs `sys.executable -m wtenv` with standard input
    closed and returns exit status, stdout, and stderr. Closed input makes any prompt fail
    the test (FR-004).
  - `docker` (session): runs `docker info` once; a test that requests it is skipped with the
    message `Docker is not available` when that fails (constitution, Technical Constraints).
- [X] T005 Add one smoke test per pytest gate in `tests/unit/test_package.py` and `tests/integration/test_git_worktrees.py`
  - Unit: `wtenv.__version__` is a non-empty string.
  - Integration (`pytestmark = pytest.mark.integration`): `make_repo` and `add_worktree`
    give a main and a linked worktree that `git worktree list --porcelain` reports.
  - Each pytest gate needs at least one test: with none collected, pytest exits 5.
- [X] T006 Run `uv sync` and make all five gates exit 0 on the empty package; keep `uv.lock` tracked
  - `ruff` also reads `contracts/json_models.py`; it passes as it is today. Do not edit it.

**Checkpoint**: `uv sync` works and the five gates exit 0.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: error codes and exit statuses, the `--json` models, git helpers, worktree
identity, the registry and its locks, and the CLI shell with a lazy-import `--version`.

**⚠️ CRITICAL**: no user story can start until this phase is complete.

### 2A. Error codes and exit statuses (`errors.py`)

- [X] T007 [P] Write failing tests for the error-code table in `tests/unit/test_errors.py`
  - The 19 codes and their statuses exactly as cli.md, "Error codes and exit statuses":
    1 `internal_error`, 2 `usage_error`, 3 `config_invalid`, 4 `not_in_worktree`,
    5 `not_provisioned`, 6 `no_free_block`, 7 `env_file_unusable`, 8 `dependency_unavailable`,
    9 `template_missing`, 10 `template_in_use`, 11 `ownership_conflict`, 12 `post_up_failed`,
    13 `partial_failure`, 14 `registry_busy`, 15 `worktree_busy`, 16 `registry_unreadable`,
    17 `problems_found`, 18 `worktree_exists`, 19 `unsupported` (FR-059).
  - Each code has exactly one status and no status is used twice; `EXIT_SUCCESS` is 0; the
    `exec` statuses are 125, 126, 127.
  - `WtenvError` carries `code`, `message`, `hint`, and `details`.
  - A fresh interpreter that imports `wtenv.errors` has imported nothing outside the standard
    library.
- [X] T008 Implement `ErrorCode`, `EXIT_STATUS`, `EXIT_SUCCESS`, the three `exec` statuses, and `WtenvError` in `src/wtenv/errors.py`
  - Standard library only (plan.md, Project Structure); names and values as in `json_models.py`.

### 2B. The `--json` models and output (`output.py`)

- [X] T009 [P] Write failing contract tests in `tests/contract/conftest.py` and `tests/contract/test_models_match_contract.py`
  - Fixture `contract`: loads `contracts/json_models.py` from its path.
  - For every model in the contract, `wtenv.output` has a model of the same name with an
    equal `model_json_schema()`; every enum has the same members and values;
    `SCHEMA_VERSION` is 1.
- [X] T010 [P] Write failing tests for printing results, errors, and warnings in `tests/unit/test_output.py`
  - With `--json`, standard output gets exactly one document, the result model serialised
    with `model_dump_json()`; everything else goes to standard error (FR-058).
  - Standard error lines: `wtenv: error [<code>]: <message>`, then `hint: …` when there is
    one; `wtenv: warning [<code>]: <message>` (cli.md, Rules for every command).
  - A failed command still prints its own result model: `ok` false, `error` set with
    `exit_status` equal to the code's status, other fields at their defaults.
- [X] T011 Port every model and enum of `json_models.py` to `src/wtenv/output.py`
  - `ErrorCode`, `EXIT_STATUS`, and the `exec` statuses are imported from `wtenv.errors`, not
    defined twice. `output.py` imports no other wtenv module. Base model: `extra="forbid"`.
- [X] T012 Implement the functions that print a result, an error, and warnings in `src/wtenv/output.py`

### 2C. Git helpers and worktree identity (`gitutil.py`, `identity.py`) — core area 3

- [X] T013 [P] Write failing tests for git output parsing in `tests/unit/test_gitutil.py`
  - `git worktree list --porcelain` text → one record per worktree with its path and the
    `bare`, `detached`, `locked`, and `prunable` attributes (research.md §6).
  - Git missing → `dependency_unavailable`, `details.dependency` `git`, `reason`
    `not_installed`. A git call that fails on a git older than 2.31 → `reason` `too_old`
    with `required` and `found`.
  - The environment given to git lacks every repository-local variable (`GIT_DIR`,
    `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, and the rest of
    `git rev-parse --local-env-vars`).
- [X] T014 [P] Write failing tests for `points_to`, the identity parser, `short_id`, and `slug` in `tests/unit/test_identity.py`
  - `points_to(p)` (data-model.md, Status and the orphan checks): `p/.git` is a directory →
    that directory; a file holding `gitdir: <path>` → the path resolved against `p`;
    missing or unreadable → nothing.
  - The three lines of `git rev-parse --path-format=absolute --absolute-git-dir
    --show-toplevel --git-common-dir` become `git_dir`, `path`, and `repository`, each
    through `os.path.realpath` (data-model.md, Worktree identity).
  - `short_id(git_dir, n)`: the first `n` hexadecimal digits of the SHA-256 of the git
    directory path (files.md: `<id8>`, `<id16>`).
  - `slug(name, separator)` (files.md, Names): the worktree directory's name
    "lowercased, with every run of characters outside `a–z` and `0–9` replaced by one `_`
    (for databases) or `-` (for compose), trimmed of those characters at both ends, and cut
    to 40 characters. An empty slug becomes `wt`."
- [X] T015 [P] Write failing integration tests for identity on real worktrees in `tests/integration/test_identity_git.py`
  - The same identity from the worktree root, a subdirectory, and through a symbolic link;
    main and linked worktrees differ and share `repository` (FR-006).
  - After `git worktree move` and after a plain `mv`, `git_dir` is unchanged and `path` is
    the new location (research.md §6).
  - Outside a repository and inside `.git`: `not_in_worktree` with `details.cwd` (FR-002).
  - An inherited `GIT_DIR` that points at another repository does not change the result.
  - Every name printed by `git rev-parse --local-env-vars` is in gitutil's scrub list.
- [X] T016 Implement `src/wtenv/gitutil.py`: run git with a scrubbed environment, the porcelain parser, `git_path`, and `is_tracked`
  - The scrub list is a constant, so no extra git call is made. The git version is read only
    after a git call fails, to report `too_old`; the normal path makes no version call
    (NFR-001).
  - `git_path(worktree, name)` runs `git rev-parse --path-format=absolute --git-path <name>`;
    `is_tracked(worktree, path)` asks git whether the path is tracked.
- [X] T017 Implement `WorktreeIdentity`, the current-worktree lookup, `points_to`, `short_id`, and `slug` in `src/wtenv/identity.py`
  - One `git rev-parse` call; exit status 128 → `not_in_worktree` with `details.cwd`.
  - `slug` and `short_id` are here, not in a story phase, because US2's database names and
    US3's compose project name both use them.

### 2D. Registry and locks (`locks.py`, `registry.py`) — core area 1

- [X] T018 [P] Write failing tests for the state directory and locks in `tests/unit/test_locks.py`
  - State directory: `platformdirs.user_state_path("wtenv", appauthor=False)`, moved by
    `XDG_STATE_HOME`; created with mode `0700`; `registry.lock` and `locks/<id16>.lock` get
    mode `0600` (files.md, State directory).
  - Registry lock: the wait bound defaults to 10 s and is a parameter; past it,
    `registry_busy` with `details.waited_seconds` (research.md §9).
  - Worktree lock: default bound 60 s, a parameter; past it, `worktree_busy`. A no-wait form
    reports "held" without raising (for `gc`, FR-077).
  - A lock whose holder was killed with `SIGKILL` can be taken at once (FR-078). Lock files
    are never deleted.
  - Locks use `fallback_to_soft=False`; a filesystem without `flock` gives
    `registry_unreadable`, reason `lock_unsupported`.
- [X] T019 [P] Write failing tests for the registry models in `tests/unit/test_registry_models.py`
  - The example in data-model.md, "Registry file", loads and dumps back to equal JSON.
  - Unknown fields are rejected (`extra="forbid"`). A field named like a password does not
    exist (FR-019).
  - One test per rule (data-model.md, Registry entry and the tables after it): `git_dir`
    equals its key; entry `state` is `incomplete` or `provisioned`; `block.start` is
    `20000 + k × size` for a whole number `k`; `block.size` is 1 to 1000 and
    `start + size - 1 ≤ 29999`; `published[].protocol` is `tcp` or `udp`; `databases` holds
    at most one record per kind; database `kind` is `postgres` or `sqlite`; every resource
    `state` is `creating`, `created`, or `removing`.
- [X] T020 [P] Write failing tests for loading and saving the registry in `tests/unit/test_registry_store.py`
  - No file → an empty registry with `version` 1, in the per-user state directory (FR-066).
  - Save writes a temporary file, `fsync`s it, and `os.replace`s it; the file has mode `0600`.
  - Not valid JSON, not matching the schema, an unknown `version`, or unreadable → stop with
    `registry_unreadable`, `details.path` and `details.reason` (`invalid_json`,
    `invalid_schema`, `unknown_version`, `not_readable`); the file is never rewritten (FR-070).
  - A transaction runs read-check-write under the registry lock; an exception inside it
    saves nothing (FR-068).
- [X] T021 Implement the state directory, the registry lock, and the worktree lock in `src/wtenv/locks.py`
- [X] T022 Implement the registry models in `src/wtenv/registry.py`
  - `Registry`: `version` (1), `worktrees` keyed by `git_dir`, `hooks` keyed by repository.
  - `WorktreeEntry`: `git_dir` "Equals the key"; `path` "Location at the last `up` or `down`
    in the worktree. Updated when the worktree moved (FR-084)"; `repository` "Common git
    directory"; `state` `"incomplete"` or `"provisioned"`; `block` "Always present once the
    entry exists"; `ports` "Same order as `ports` in `wtenv.toml`"; `published` "Empty
    without compose"; `env_file` `EnvFileRecord` or null; `databases` "At most one per kind.
    A kind no longer configured stays here until `down` (FR-065)"; `compose`
    `ComposeRecord` or null; `exclude_patterns` "This worktree's generated paths, as written
    to `.git/info/exclude`".
  - `PortBlock`: `start` "`20000 + k × size` for some whole number `k`"; `size` "1 to 1000;
    `start + size - 1 ≤ 29999`".
  - `VariablePort`: `variable`, `port`. `PublishedPort`: `service`, `target` (container
    port), `protocol` (`tcp` or `udp`), `host_ip` (or null), `port` (the assigned host port),
    `variable` (the variable it is tied to, or null).
  - `EnvFileRecord`: `path` "Relative to the worktree root"; `created_file`; `added_newline`;
    `state`.
  - `DatabaseRecord`: `kind` `"postgres"` or `"sqlite"`; `name` (Postgres) or null; `host`,
    `port`, `user` (Postgres, "From the URL pattern at creation") or null; `path` (SQLite,
    "Copy's path, relative to the worktree root") or null; `state`.
  - `ComposeRecord`: `project`, `file`, `override` (both relative to the worktree root),
    `override_state`. `HookRecord`: `hook_file` (absolute path), `created_file`.
  - Resource states `creating`, `created`, `removing` use `ResourceState` from
    `wtenv.output`. No password field anywhere (FR-019).
- [X] T023 Implement registry `load`, atomic `save`, and the transaction in `src/wtenv/registry.py`

### 2E. CLI shell and `--version` (`cli.py`, `__main__.py`)

- [X] T024 [P] Write failing tests for the CLI shell in `tests/contract/test_cli_shell.py`
  - `wtenv --version` prints `wtenv <version>`; `wtenv --version --json` prints a
    `VersionResult`; both exit 0. `python -m wtenv` behaves like `wtenv`.
  - An unknown command or option exits 2. With `--json` before any `--`, stdout holds one
    document with `error.code` `usage_error`, and `command` null when no known command was
    named. `--json` at the root is accepted only with `--version`.
  - An unexpected exception inside a command exits 1 with `internal_error` and
    `details.exception` set to the class name.
  - `--help` works; no shell-completion option is offered (cli.md, Rules for every command).
- [X] T025 [P] Write failing lazy-import tests in `tests/unit/test_lazy_imports.py`
  - In a fresh interpreter, after `wtenv.cli.main(["--version"])`: `pydantic`, `filelock`,
    `platformdirs`, `psycopg`, and `click` are not in `sys.modules` (NFR-001; research.md §7,
    §8).
- [X] T026 Implement the Typer app, `main`, `--version`, and exit-status mapping in `src/wtenv/cli.py`; add `src/wtenv/__main__.py`
  - `main(argv)` runs the app with `standalone_mode=False` and returns the exit status:
    `WtenvError` → its status; a Typer usage error → `usage_error`; anything else →
    `internal_error` (plan.md, Design overview).
  - `--json` is detected by scanning the arguments before any `--`, so a usage error still
    prints one JSON document.
  - Module-level imports: `typer`, the standard library, `wtenv.__version__`, and
    `wtenv.errors` only. Each command imports its implementation inside its function.
    `add_completion=False`. Never `import click` (research.md §7).

**Checkpoint**: the five gates pass; `uv run wtenv --version` prints the version.

---

## Phase 3: User Story 1 - Port and env isolation (Priority: P1) 🎯 MVP

**Goal**: `wtenv up` gives the worktree its own port block and writes its port variables to
its env file, with no configuration. Running it again changes nothing.

**Independent Test**: in a repository with no `wtenv.toml`, create two worktrees and run
`wtenv up` in each: the blocks share no port, `.env.local` sets `PORT` in each, and a dev
server reading `PORT` runs in both at once. A repeat `up` leaves ports and env file
byte-identical. Automated by T039–T043; manual by quickstart.md sections 1 and 2.

### 3A. Configuration: `ports`, `block_size`, `env_file` (`config.py`) — core area 4

- [X] T027 [P] [US1] Write failing tests for configuration loading in `tests/unit/test_config.py`
  - No `wtenv.toml`: `ports = ["PORT"]`, `block_size = 10`, `env_file = ".env.local"` (FR-005, FR-063).
  - `ports` (config.md): "At least one name. Names are unique and match
    `[A-Za-z_][A-Za-z0-9_]*`. `DATABASE_URL` is not allowed."
  - `block_size`: "1 to 1000".
  - `env_file`: "A relative path inside the worktree; `..` may not leave the worktree."
  - An unknown key, a wrong type, or invalid TOML → `config_invalid` with `details.file`
    and `details.setting` (dotted name); nothing is read further (FR-064).
- [X] T028 [US1] Implement `Config` and loading of `ports`, `block_size`, and `env_file` in `src/wtenv/config.py`
  - `tomllib` and a pydantic model with `extra="forbid"`; validation errors become
    `config_invalid` naming the setting. The file is read from the worktree root (FR-062).

### 3B. Port allocation (`ports.py`) — core area 2

- [X] T029 [P] [US1] Write failing tests for the free-port test and the block search in `tests/unit/test_ports_search.py`
  - A port is free when a TCP `bind` without `SO_REUSEADDR` succeeds on `127.0.0.1`,
    `0.0.0.0`, and `::1`; an unavailable address family (`EADDRNOTAVAIL`, `EAFNOSUPPORT`) is
    skipped (data-model.md, Port allocation; FR-009).
  - Candidates `20000 + k × size` while `20000 + k × size + size - 1 ≤ 29999`; a candidate
    that overlaps any registry block of any repository (FR-008) or holds a busy port is
    skipped; the first one left is taken (FR-007, FR-010).
  - None left → `no_free_block` with `details.block_size` and `details.range` (FR-012).
  - The same registry and the same free ports give the same block. The search takes the
    free-port test as a parameter, so tests can fake it.
- [X] T030 [P] [US1] Write failing tests for assigning variable ports in `tests/unit/test_ports_assign.py`
  - Variable `i` (from 0) in `ports` gets `start + i`, the same on every call (FR-014, FR-017).
  - More variables than the block holds → `config_invalid`, `details.setting` `block_size`,
    `details.min_block_size` the number needed (FR-014). No port outside the block (FR-011).
- [X] T031 [US1] Implement the free-port test and the block search in `src/wtenv/ports.py`
- [X] T032 [US1] Implement assigning variable ports in `src/wtenv/ports.py`

### 3C. Env file section (`envfile.py`) — core area 8

- [X] T033 [P] [US1] Write failing tests for writing and reading wtenv's section in `tests/unit/test_envfile_write.py`
  - No file → created with mode `0600`, holding only the section: the two marker lines of
    FR-016 and one `NAME=value` line per variable, each ending in `\n` (FR-019).
  - Existing developer lines are kept byte for byte and the section is appended at the end;
    a file without a final line break gets one first, and that is reported (`added_newline`).
  - An existing section is rewritten where it is, even after it was moved or lines were added
    after it; lines edited inside it are restored; the same values twice give identical bytes
    (FR-017, FR-079).
  - Markers are recognised with trailing whitespace or a trailing carriage return.
  - Damaged markers (a begin with no end, an end with no begin, two sections) →
    `env_file_unusable`, reason `markers_damaged`, file unchanged (FR-081).
  - Quoting (files.md, Env file section): a value made only of
    `A–Z a–z 0–9 _ . / : @ % + = , ~ -` is bare; any other value is in single quotes.
  - An existing file keeps its mode; writing uses a temporary file and an atomic rename of the
    real path, so a symbolic link stays a link.
  - Reading (reading R1; files.md, Env file section): `read_section` returns the section's
    variables in file order. A bare value and a single-quoted value each round-trip: what
    `write_section` wrote, `read_section` returns unchanged, quotes removed. Lines outside
    the section are not returned. A value edited by hand inside the section is returned as
    it stands.
  - Reading a file with damaged markers → `env_file_unusable`, reason `markers_damaged`; a
    missing file → `env_file_unusable`, reason `missing`; a file with neither marker line →
    `env_file_unusable`, reason `no_section` (cli.md, `wtenv exec`).
- [X] T034 [P] [US1] Write failing tests for removing the section and finding duplicates in `tests/unit/test_envfile_remove.py`
  - Removing takes out the section, both markers included, and nothing else; a recorded
    `added_newline` is removed again (FR-079).
  - A file that wtenv created and that holds nothing else afterwards is deleted (FR-038).
  - No file or no section → already absent, not an error (FR-042). Damaged markers → file
    unchanged and the failure returned to the caller (FR-081).
  - A managed variable also defined outside the section is reported by name; that line is
    left unchanged (FR-080).
- [X] T035 [US1] Implement `write_section` and `read_section` in `src/wtenv/envfile.py`
  - `read_section(path)` returns the section's `(name, value)` pairs with quoting removed;
    `exec` uses it (reading R1).
- [X] T036 [US1] Implement removing the section and the duplicate check in `src/wtenv/envfile.py`

### 3D. `.git/info/exclude` block (`exclude.py`)

- [X] T037 [P] [US1] Write failing tests for the exclude block in `tests/unit/test_exclude.py`
  - Adding patterns creates or extends the block (files.md, `.git/info/exclude` block): the
    same two marker lines as the env file; inside, the sorted union of the patterns, each
    starting with `/`; every other line kept; a missing file or `info/` directory created.
  - Adding the same patterns again changes nothing; adding never removes a line.
  - Removing the block takes out the markers and the lines between them, nothing else (FR-085).
  - Damaged markers → `unsupported`, reason `markers_damaged`.
- [X] T038 [US1] Implement adding patterns (`add_patterns`) and removing the block in `src/wtenv/exclude.py`

### 3E. `wtenv up` (`provision.py`, `output.py`, `cli.py`)

- [X] T039 [P] [US1] Write failing integration tests for the US1 acceptance scenarios in `tests/integration/test_us1_ports_env.py`
  - Scenario 1 (FR-005, FR-007, FR-015): `up` allocates a block; `.env.local` sets `PORT` to
    a port in it with mode `0600`; the `UpResult` reports the block and each change.
  - Scenario 2 (FR-008): two worktrees get blocks that share no port.
  - Scenario 3 (FR-011, FR-017, SC-006): a repeat `up`, also from a subdirectory, keeps the
    ports, leaves the env file byte-identical, and reports every change as `unchanged`.
  - Scenario 4 (FR-009): with a listener on a port of the first candidate, the block avoids it.
  - Scenario 5 (FR-014): `ports = ["PORT", "API_PORT", "VITE_PORT"]` gives three different
    ports in that order, the same on every run.
  - Scenario 6 (FR-016): the developer's lines are unchanged and the section follows them.
  - Scenario 7 (FR-015): `env_file` set to another path writes there; `.env.local` is not
    created.
  - The main worktree works like any other; outside a worktree → exit 4, nothing changed
    (FR-002). `git status --porcelain` is empty in both worktrees (FR-018). `up --json`
    prints one document that validates as `UpResult` (FR-058). SC-009.
- [X] T040 [P] [US1] Write failing concurrency tests in `tests/integration/test_concurrency.py`
  - SC-005, FR-013: five `up` processes started at once in five worktrees get blocks that
    share no port; 20 trials, each with a fresh state directory.
  - Two `up` processes at once in one worktree: both exit 0, one block, the env file a single
    run writes (spec Edge Cases).
  - `up` called in-process with a small worktree-lock bound while another process holds the
    lock → `worktree_busy`, nothing changed; after that holder is killed, `up` runs at once
    (FR-077, FR-078).
- [X] T041 [P] [US1] Write failing recovery tests for `up` in `tests/integration/test_recovery.py`
  - Write order (data-model.md, Write order of `up`). Run `up` in a child process that is
    stopped with `os._exit(137)` at one point, then run `up` normally. The four points:
    - after the registry step: the first call of `wtenv.registry.save` runs, then the
      process exits (entry saved, exclude block not written);
    - after the exclude step: `wtenv.exclude.add_patterns` runs, then the process exits
      (exclude block written, env file not written);
    - after the env-file step: `wtenv.envfile.write_section` runs, then the process exits
      (section written, its record still `creating`);
    - before the completion step: the call of `wtenv.registry.save` that marks the entry
      `provisioned` is replaced by the exit (env-file record `created`, entry not yet
      `provisioned`).
  - At every point, the entry exists with `state` `incomplete` and a block, and every
    resource that exists was recorded before it was created: each line of the exclude block
    is in the entry's `exclude_patterns`, and an env file or section that exists has an
    env-file record. Nothing exists that the registry does not record (FR-067).
  - The second run finishes the work: it exits 0, the exclude block and the section are
    written, the env-file record is `created`, the entry ends `provisioned`, and the block is
    the one recorded at the interruption (FR-069).
- [X] T042 [US1] Add failing tests for env-file and configuration errors to `tests/integration/test_us1_ports_env.py`
  - Env file path is a directory, has no parent directory, is not writable, is tracked by git,
    or has damaged markers → exit 7 with `details.reason` `is_directory`, `parent_missing`,
    `not_writable`, `tracked_by_git`, or `markers_damaged`; file and registry unchanged
    (FR-018, FR-081). Skip the not-writable case when running as root.
  - An unknown setting, or more variables than the block holds → exit 3, the second with
    `details.min_block_size`; nothing changes, on a first `up` and on a provisioned worktree.
  - A managed variable also set outside the section → warning `env_duplicate_variable` naming
    it, in stderr and in `warnings` (FR-080).
  - A registry file that is not JSON → exit 16; the file is not rewritten (FR-070).
  - `block_size = 1000` leaves ten candidates; with a listener in each (ports already taken
    count as taken) → exit 6 `no_free_block`, nothing allocated (FR-012).
- [X] T043 [US1] Add failing tests for configuration changes and moves to `tests/integration/test_us1_ports_env.py`
  - FR-065 (config.md, Changes after provisioning): a changed `ports` list is reassigned
    within the same block; a changed `block_size` allocates a new block, releases the old
    one, and reports both; a changed `env_file` writes the section to the new file and
    removes it from the old one.
  - Two worktrees on branches with different `wtenv.toml` are each provisioned from their
    own (FR-062).
  - After `git worktree move`, `up` keeps the block, records the new location, and warns
    `worktree_moved` (FR-084).
  - A port of the block later taken by another process: a repeat `up` keeps the block (FR-011).
- [X] T044 [US1] Implement the checks of `up` that change nothing in `src/wtenv/provision.py`
  - cli.md, `wtenv up`, steps 1–4 and 6: identify the worktree; take the worktree lock
    (bound a parameter, default 60 s); load `wtenv.toml` or the defaults; check the env file
    path (parent present, not a directory, not tracked by git, writable, markers intact);
    check that the block size holds all port variables. Nothing changes unless all pass.
- [X] T045 [US1] Implement the registry step of `up` in `src/wtenv/provision.py`
  - Step 8, in one registry transaction: create the entry as `incomplete`, or record a new
    location for a known identity (FR-084); allocate a block when the entry has none; assign
    ports; record the env file as `creating` and its pattern in `exclude_patterns`; save.
    The search and the write share the transaction (FR-013); the registry lock is held only
    for this transaction and for writing the exclude block (FR-068).
  - Only after that save: add the entry's `exclude_patterns` to the exclude block, under the
    registry lock (files.md). Nothing is created before the entry is saved (data-model.md,
    Write order of `up`).
- [X] T046 [US1] Implement the env-file step and completion of `up` in `src/wtenv/provision.py`
  - Step 12: write the section; mark the env-file record `created` with `created_file` and
    `added_newline` as observed. Step 14: mark the entry `provisioned`.
  - A repeat `up` with nothing to change stays `provisioned` throughout and writes nothing
    (data-model.md, Entry states).
  - Return an `UpResult`: worktree view, one change per item (`created`, `updated`,
    `unchanged`, `released`), warnings.
- [X] T047 [US1] Apply configuration changes in `up` in `src/wtenv/provision.py`
  - `ports`, `block_size`, and `env_file` changes as in T043 (FR-065). A changed block size is
    checked against the new size before anything changes (FR-014).
- [X] T048 [US1] Add the `up` text output (`provisioned`, `ports`, `env file` lines of cli.md) to `src/wtenv/output.py`
- [X] T049 [US1] Add `wtenv up [--json]` to `src/wtenv/cli.py`
  - Imports `wtenv.provision` inside the function. Exit statuses so far: 0, 3, 4, 6, 7, 14,
    15, 16.

**Checkpoint**: the MVP. US1 works on its own; the five gates pass.

### 3F. Symbolic links in `up` (`identity.py`, `provision.py`) — FR-086

Added after the review of the destructive paths (2026-10-05, finding MEDIUM-1). Run this
group after Phase 7, then group 6G.

- [X] T151 [P] [US1] Add failing tests for the symbolic-link check to `tests/unit/test_identity.py`
  - FR-086: `symlinked_part(root, relative)` returns the first of the worktree root, each
    directory below it, and the path itself that is a symbolic link, or None. A dangling
    link counts. A path that does not exist, with no link on the way, gives None.
  - A link above the root does not count: a root reached through a link is resolved first
    (FR-006).
- [X] T152 [US1] Add failing tests for `up` refusing symbolic links to `tests/integration/test_us1_ports_env.py`
  - FR-086: `.env.local` a symbolic link to a file outside the worktree → exit 7,
    `details.reason` `symlink`, `details.path` the link; the target byte-identical; no
    registry entry created.
  - `env_file = "config/.env.local"` with `config` a link to a directory → the same.
  - SQLite configured, `.wtenv` a link to another worktree's `.wtenv/` → exit 7; the other
    worktree's copy byte-identical.
  - A provisioned worktree whose `.env.local` is then replaced by a link: a repeat `up` →
    exit 7; the target and the registry byte-identical.
  - A changed `env_file` (FR-065) whose old recorded file is now a link → exit 7, nothing
    removed from it.
  - With `docker`: the override path a link → exit 7, before anything is changed for compose.
- [X] T153 [US1] Implement `symlinked_part` in `src/wtenv/identity.py`
  - Walk from the root down with `os.lstat`, no `realpath`.
- [X] T154 [US1] Refuse symbolic links in the checks of `up` in `src/wtenv/provision.py`
  - cli.md, `wtenv up`, steps 4, 5, and 7: the env file, the override path, `.wtenv/` and
    the SQLite copy, and a recorded env file or override that a configuration change would
    remove → `env_file_unusable`, reason `symlink`, `details.path` the link. Nothing changes
    unless all checks pass.
  - Remove the symbolic-link case of T033 from `tests/unit/test_envfile_write.py`: files.md
    no longer promises that a link stays a link.

---

## Phase 4: User Story 2 - Database isolation (Priority: P2)

**Goal**: with `[database]` in `wtenv.toml`, `up` gives the worktree its own Postgres
database or SQLite file, copied from a template, and sets `DATABASE_URL`. Post-up commands
run after provisioning (plan.md: delivered with US2).

**Independent Test**: configure Postgres with a template, run `up` in two worktrees, change
the schema in one: the other and the template are unchanged. Repeat with SQLite. Automated by
T059–T060, T062, T063, and T067. US2 scenario 5 (`down` removes only this database) needs
`down`, which plan.md delivers with US4; it is tested in T095 (SQLite) and T097 (Postgres).

### 4A. Configuration: `[database]` and `post_up` (`config.py`) — core area 4

- [X] T050 [P] [US2] Write failing tests for the database table and `post_up` in `tests/unit/test_config_database.py`
  - `database.type` `"postgres"` or `"sqlite"`, `database.template`, and `database.url` are
    required (config.md, Settings).
  - Placeholders `{name}`, `{path}`, `{env:NAME}`: "Any other text in braces is
    `config_invalid`."
  - Postgres patterns: scheme `postgresql://` or `postgres://`, with or without a driver
    suffix such as `postgresql+psycopg://`; `{name}` as the database name; "name the host
    explicitly as `localhost`, `127.0.0.1`, or `[::1]`. Any other host, a missing host, a
    `host`, `hostaddr`, or `service` query parameter, or a Unix-socket directory is
    `config_invalid`" (FR-025).
  - SQLite patterns "must contain `{path}`".
  - `post_up`: a list of shell commands; "Each must be non-empty."
  - Each error names its setting (`database.url`, `database.type`, `post_up`).
- [X] T051 [US2] Add the `database` table and `post_up` to `src/wtenv/config.py`

### 4B. Names and the URL pattern (`database.py`)

- [X] T052 [P] [US2] Write failing tests for database names in `tests/unit/test_names.py`
  - Postgres name `wtenv_<slug>_<id8>` (`slug` with `_`, from T017); SQLite copy
    `<worktree>/.wtenv/<template file name>` (FR-022).
- [X] T053 [P] [US2] Write failing tests for resolving the URL pattern in `tests/unit/test_database_url.py`
  - `{name}` → database name; `{path}` → absolute path of the copy; `{env:NAME}` → the
    variable's value; unset → `config_invalid` with `details.variable` (FR-026).
  - A resolved URL with a single quote or a line break → `config_invalid`.
  - wtenv's own connection: the pattern's user, password, host, and port, and the database
    `postgres`; the driver suffix is dropped for it and kept in `DATABASE_URL`; query
    parameters are kept in `DATABASE_URL` and not used (config.md).
- [X] T054 [US2] Implement database names and URL-pattern resolution in `src/wtenv/database.py`
  - Names use `identity.slug` and `identity.short_id` (T017). `psycopg` is never imported at
    module level (NFR-001).

### 4C. Creating SQLite copies (`database.py`) — core area 5

- [X] T055 [P] [US2] Write failing tests for SQLite creation in `tests/unit/test_database_sqlite.py`
  - The template (relative to the worktree root, or absolute) is copied byte for byte to
    `<worktree>/.wtenv/<file name>` through a temporary file and an atomic rename (FR-021;
    research.md §11).
  - Template missing → `template_missing`, `details.kind` `sqlite`, `details.template`;
    nothing created (FR-083).
  - A non-empty `-wal` or `-journal` file beside the template → `template_in_use`.
  - Something at the target path that is not recorded → `ownership_conflict`,
    `details.kind` `sqlite_file`, `details.name`; it is not modified (FR-024).
  - A recorded copy that exists is left alone (FR-023); the template is never modified (FR-027).
- [X] T056 [US2] Implement SQLite creation in `src/wtenv/database.py`

### 4D. Creating Postgres databases (`database.py`) — core area 5

- [X] T057 [P] [US2] Add the `postgres_server` fixture to `tests/integration/conftest.py`
  - Session scope; requests `docker`; starts `postgres:17` with testcontainers, published on
    `127.0.0.1`; creates a template database with one small table; yields host, port, user,
    and password.
- [X] T058 [P] [US2] Write failing tests for Postgres error mapping in `tests/unit/test_database_postgres_errors.py`
  - research.md §3 table: `55006` → `template_in_use`; `3D000` → `template_missing`;
    `42P04` → `ownership_conflict`; cannot connect, authentication failure, `42501`, or a
    server older than 13 → `dependency_unavailable`, `details.dependency` `postgres`,
    `reason` `cannot_connect`, `authentication_failed`, `permission_denied`, or `too_old`.
  - The `42501` message says the template needs `IS_TEMPLATE` or ownership (research.md §3).
  - No message or detail contains the password (FR-019).
- [X] T059 [P] [US2] Write failing Postgres creation tests in `tests/integration/test_us2_database.py`
  - Request `postgres_server`. Scenario 1 (FR-020): database `wtenv_<slug>_<id8>` exists,
    holds the template's rows, and `DATABASE_URL` names it.
  - Scenario 2: a schema change in one worktree leaves the other's database and the template
    unchanged. Scenario 3 (FR-023, SC-006): a repeat `up` keeps the database and its rows.
  - The password comes from `{env:NAME}` (FR-026) and is in no output and not in the
    registry (FR-019); `UpResult` shows kind, name, host, and port.
- [X] T060 [US2] Add failing Postgres failure tests to `tests/integration/test_us2_database.py`
  - Scenario 6 (FR-024): an unrecorded database with the target name → exit 11, unmodified.
  - Scenario 7: a closed port → exit 8; once the server answers, `up` completes.
  - Scenario 8 (FR-082, FR-027): another session on the template → exit 10 within 2 s,
    `details.connections` the count, the session still connected, no database created or
    recorded, `DATABASE_URL` not written, block kept, entry `incomplete`; after the session
    closes, `up` completes.
  - A host other than `localhost`, `127.0.0.1`, or `[::1]` → exit 3 before any connection
    (FR-025).
- [X] T061 [US2] Implement the Postgres connection and `CREATE DATABASE … TEMPLATE` in `src/wtenv/database.py`
  - One autocommit connection to the `postgres` maintenance database, never to the template
    (research.md §3). `psycopg` imported inside the functions.
  - Before `CREATE DATABASE <name> TEMPLATE <template>` (no `STRATEGY`): count other sessions
    on the template in `pg_stat_activity`; above zero → `template_in_use` at once, naming
    the template and the count; nothing issued (FR-082). Names quoted with
    `psycopg.sql.Identifier`.
  - Template absent from `pg_database` → `template_missing`; a target name that exists is
    reported so the caller can raise `ownership_conflict`. Errors mapped as in T058.

### 4E. The database step of `up` (`provision.py`)

- [X] T062 [US2] Add failing SQLite tests to `tests/integration/test_us2_database.py`
  - Scenario 4 (FR-021, FR-022): the worktree's copy exists, `DATABASE_URL` points to it
    after the port variables, and writing to it changes neither the template nor another
    worktree's copy. Scenario 3 for SQLite: a repeat `up` keeps the copy's data.
  - Scenario 6 for SQLite: an unrecorded file at the target → exit 11, file unchanged.
  - Template missing → exit 9; template with a non-empty `-wal` → exit 10. In both, nothing
    recorded for the database, no `DATABASE_URL`, block kept, entry `incomplete`; a later
    `up` completes (FR-083).
  - `git status --porcelain` stays empty: `/.wtenv/` is in the exclude block (FR-018).
  - Config changes (config.md): a new `database.template` or `database.url` rewrites
    `DATABASE_URL` and keeps the database; a new `database.type` creates the new kind and
    keeps the old one recorded; removing `[database]` removes `DATABASE_URL` and keeps the
    database recorded (FR-065).
- [X] T063 [US2] Add failing recovery tests for the database step to `tests/integration/test_recovery.py`
  - `up` interrupted (as in T041) after the database is recorded as `creating` but before it
    exists, and after it exists but before it is marked `created`: the next `up` creates or
    keeps it and marks it `created`; no database exists that the registry does not record
    (FR-067, FR-069; data-model.md, Resource states). SQLite always; Postgres with Docker.
- [X] T064 [US2] Implement the database checks and the database step in `src/wtenv/provision.py`
  - Step 7, before anything changes: the URL pattern resolves and names a local host.
  - Step 10, when no database of the configured kind is recorded: check the template exists
    and is not busy, check nothing exists at the target (`ownership_conflict`), record it as
    `creating`, create it, mark it `created`. `template_in_use` and `template_missing` record
    nothing; if the server answers `55006` after the record was written, remove the record
    again (FR-082). The worktree lock is held during the copy; the registry lock is not
    (FR-068, FR-076).
  - A recorded database is kept as it is (FR-023); `up` never removes a database
    (data-model.md, Resource states). SQLite adds `/.wtenv/` to the exclude patterns in the
    step-8 save, before the exclude block is written (data-model.md, Write order of `up`).
- [X] T065 [US2] Write `DATABASE_URL` and apply database configuration changes in `src/wtenv/provision.py`
  - The section holds the port variables in `ports` order, then `DATABASE_URL` when a database
    is configured and created (files.md). Config changes as in T062 (FR-065).
- [X] T066 [US2] Show the database (kind, name or path, host, port; never a URL) in the `up` output in `src/wtenv/output.py`

### 4F. Post-up commands (`provision.py`)

- [X] T067 [US2] Add failing post-up tests to `tests/integration/test_us2_database.py`
  - FR-036: commands run in order through `sh -c`, from the worktree root, with the
    worktree's variables set, standard input closed, and their standard output sent to
    wtenv's standard error, on every successful `up`, including a repeat one (cli.md).
  - FR-037: the first non-zero exit stops `up` with exit 12, `details.command` and
    `details.exit_status`; later commands do not run; resources stay; the entry is
    `incomplete`; `UpResult.post_up` lists each command run.
  - With `--json`, stdout still holds exactly one document.
- [X] T068 [US2] Implement post-up commands (step 13) in `src/wtenv/provision.py`

**Checkpoint**: US1 and US2 work; the five gates pass.

---

## Phase 5: User Story 3 - docker-compose isolation (Priority: P3)

**Goal**: with `[compose]` in `wtenv.toml`, `up` gives the worktree its own compose project
name and an auto-loaded override that moves every host port into the worktree's block.

**Independent Test**: with a compose file that publishes a host port and declares a named
volume, run `up` and then plain `docker compose up` in two worktrees: two stacks run at once,
each on its own block's ports, with separate containers, networks, and volumes. Automated by
T079–T080.

### 5A. Configuration: `[compose]` (`config.py`) — core area 4

- [X] T069 [P] [US3] Write failing tests for the compose table in `tests/unit/test_config_compose.py`
  - `compose.file` is required: "Relative path of the compose file inside the worktree. Its
    file name must be `compose.yaml`, `compose.yml`, `docker-compose.yaml`, or
    `docker-compose.yml`" (config.md); any other name → `config_invalid` naming
    `compose.file` (research.md §4, v1 limits).
- [X] T070 [US3] Add the `compose` table to `src/wtenv/config.py`

### 5B. Reading the compose model (`compose.py`)

- [X] T071 [P] [US3] Write failing tests for parsing the resolved compose model in `tests/unit/test_compose_model.py`
  - Input: documents shaped like `docker compose config --format json` output (research.md §4,
    Observed). Output: one record per mapping with `service`, `target`, `protocol`,
    `host_ip`, the published value, and the other fields Compose reported (`mode`, `name`,
    `app_protocol`).
  - A mapping without `published` (`"3000"`) is kept as having no host port.
  - A published range such as `"7000-7005"` → `unsupported`, reason `compose_port_range`,
    `details.service` (FR-033).
  - A service with `container_name` → warning `compose_fixed_container_name` naming it.
  - The resolution command: `docker compose -f <file> --profile "*" config --format json`,
    with variable `i` set to the marker value `i + 1` (data-model.md, Port allocation).
- [X] T072 [US3] Implement resolving and parsing the compose model in `src/wtenv/compose.py`
  - Compose runs as a subprocess; no YAML library, no Docker SDK (research.md §4, §7).

### 5C. Ports for published ports (`ports.py`) — core area 2

- [X] T073 [P] [US3] Write failing tests for assigning published ports in `tests/unit/test_ports_published.py`
  - A published port equal to marker `i + 1` is tied to variable `i` and gets its port (FR-031).
  - Every other published port, including one with no host port, gets the next port after
    the variables, in the order (service, container port, protocol, host IP).
  - Two mappings with the same protocol and host IP on one port → `unsupported`, reason
    `compose_port_clash`.
  - Too many ports → `config_invalid` with
    `min_block_size = len(ports) + untied published ports` (FR-014, FR-032).
  - Same input, same result.
- [X] T074 [US3] Extend port assignment to published ports in `src/wtenv/ports.py`

### 5D. The override file (`compose.py`) — core area 6

- [X] T075 [P] [US3] Write failing override tests in `tests/unit/test_compose_override.py` with the golden file `tests/unit/golden/compose.override.yaml`
  - The golden file is the example in files.md, "Compose override file", byte for byte; the
    matching input generates exactly it, and generating twice gives identical bytes (US3
    scenario 4).
  - `name:` is `wtenv-<slug>-<id8>` (slug with `-`); one entry per service that publishes
    ports, in service-name order; `ports: !override`; each mapping repeats every field
    Compose reported, with only `published` changed; strings in JSON quoting (FR-029).
  - Override file name: `compose.override.yaml` for `compose.yaml`, `compose.override.yml`
    for `compose.yml`, `docker-compose.override.yaml` for `docker-compose.yaml`,
    `docker-compose.override.yml` for `docker-compose.yml`.
- [X] T076 [US3] Implement the project name, the override text, and the override file name in `src/wtenv/compose.py`
  - The project name uses `identity.slug` and `identity.short_id` (T017), so US3 does not
    depend on US2.

### 5E. Compose limit checks (`compose.py`)

- [X] T077 [P] [US3] Write failing tests for the compose limit checks in `tests/unit/test_compose_checks.py`
  - Docker endpoint (FR-035; research.md §11): `DOCKER_HOST` if set, otherwise
    `docker context inspect --format '{{.Endpoints.docker.Host}}'`; `unix://`, and `tcp://`
    to `localhost`, `127.0.0.1`, or `[::1]`, pass; anything else → `dependency_unavailable`,
    `dependency` `docker`, `reason` `not_local`.
  - Docker missing or not running → `not_installed` or `not_running`; Compose older than
    2.24.4 → `too_old` with `required` and `found`.
  - Default name: covered by T069.
  - Existing override: any of the four override names beside the compose file that the
    registry does not record as wtenv's → `ownership_conflict`, `kind` `compose_override`,
    `name` the path; the recorded one passes.
  - `COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` in the environment, or in `.env` beside the
    compose file → `unsupported`, reason `compose_env_override`, with `details.file` for
    `.env` (research.md §4, v1 limits).
  - The checks take the command runner and the environment as parameters; no Docker needed.
- [X] T078 [US3] Implement the Docker, Compose-version, override-file, and environment checks in `src/wtenv/compose.py`

### 5F. The compose step of `up` (`compose.py`, `provision.py`, `output.py`)

- [X] T079 [P] [US3] Write failing integration tests for US3 in `tests/integration/test_us3_compose.py`
  - Request `docker`. Scenario 1 (FR-028, FR-029): a project name no other worktree has;
    every host-published port in the override is a port of the block; container ports
    unchanged.
  - Scenario 2 (FR-030): plain `docker compose config` in each of two worktrees shows that
    worktree's project and ports; `docker compose up -d` in both runs two stacks with
    separate containers, networks, and volumes (the test removes them afterwards).
  - Scenario 3 (FR-031): `"${CACHE_PORT:-6379}:6379"` with `CACHE_PORT` in `ports`: the env
    file and the override carry the same number.
  - Scenario 4: a repeat `up` leaves the project name and the override byte-identical.
  - Scenario 5 (FR-029): the compose file is unchanged and `git status --porcelain` is empty.
  - FR-034: after `up` no container carries the project's label. Untied published ports
    appear in the `UpResult` ports with `service`, `target`, and `protocol` (FR-031).
- [X] T080 [US3] Add failing tests for the compose limits to `tests/integration/test_us3_compose.py`
  - Each fails before anything changes for the compose step (no override, no compose record):
    another compose file name → exit 3 naming `compose.file`; a developer's own override →
    exit 11, unmodified; `COMPOSE_PROJECT_NAME` in the environment, and `COMPOSE_FILE` in
    `.env`, → exit 19; `"8000-9000:80"` → exit 19 naming the service (FR-033);
    `DOCKER_HOST=tcp://192.0.2.1:2375` → exit 8 `not_local`, never contacted (FR-035).
  - Variables plus published ports above the block size → exit 3 with
    `details.min_block_size`, on a first `up` and a provisioned worktree (FR-032).
  - `container_name` → success with warning `compose_fixed_container_name`.
  - Config changes (config.md): a new `compose.file` moves the override and keeps the
    project name; removing `[compose]` removes the override and keeps the project recorded.
- [X] T081 [US3] Implement writing and verifying the override in `src/wtenv/compose.py`
  - Write through a temporary file and an atomic rename. Verify with a plain
    `docker compose --profile "*" config --format json` in the compose file's directory: the
    recorded project name and only assigned published ports; otherwise remove the override
    just written and fail with `unsupported`, reason `compose_verification_failed`
    (research.md §4; FR-033).
- [X] T082 [US3] Implement the compose checks and the compose step in `src/wtenv/provision.py`
  - Step 5 before anything changes; step 6 counts published ports; step 8 records
    `published` and the override's pattern in `exclude_patterns`, saved before the exclude
    block is written; step 11 writes and verifies.
  - The project name is recorded before anything else is done for compose and never changed
    (data-model.md, Compose record); the override is recorded as `creating` before it is
    written. No container is started (FR-034). Compose code is imported only when
    `[compose]` is configured (research.md §8).
- [X] T083 [US3] Apply compose configuration changes in `up` in `src/wtenv/provision.py`
  - `compose.file` changed: the override moves, the project name stays. `[compose]` removed:
    wtenv's override file is removed, the project stays recorded until `down` (FR-065).
- [X] T084 [US3] Show published ports and the compose project in the `up` output in `src/wtenv/output.py`

**Checkpoint**: US1–US3 work; the five gates pass.

---

## Phase 6: User Story 4 - Lifecycle and cleanup (Priority: P4)

**Goal**: `down` releases everything wtenv made for a worktree; `gc` releases worktrees git
confirms are gone; `ls` lists every worktree with its status.

**Independent Test**: provision three worktrees, `down` one, `git worktree remove` the other
two, run `gc`: nothing of the three remains and nothing else was touched. A worktree whose
directory was deleted by hand keeps its database and entry. Automated by T092–T099,
T104–T108, and T112.

### 6A. Classification (`orphans.py`) — core area 7

- [X] T085 [P] [US4] Write failing tests for `classify` in `tests/unit/test_classify.py`
  - Every row of data-model.md, "Status and the orphan checks", with hand-made directories
    and canned listings: no listing → `unverifiable`, `repository_not_found`;
    `points_to(entry.path)` is `entry.git_dir` → `provisioned` or `incomplete` by
    `entry.state`; git dir still present and path missing but listed → `unverifiable`,
    `git_still_lists`; git dir present otherwise → `unverifiable`, `moved`, with
    `current_path` when a listed path points to it; git dir gone and something at the path →
    `path_exists`; git dir gone and the path listed → `git_still_lists`; otherwise
    `orphaned` (FR-045, FR-072).
  - `classify` changes nothing.
- [X] T086 [US4] Implement `classify(entry, listing)` in `src/wtenv/orphans.py`

### 6B. Removing databases (`database.py`) — core area 5

- [X] T087 [P] [US4] Write failing tests for database removal in `tests/unit/test_database_remove.py`
  - SQLite (FR-039; constitution v1.0.2, Principle II): removing a recorded copy deletes it
    and each existing side file in the same directory named `<copy file name>-wal`, `-shm`,
    or `-journal`; the result has one item per file, `kind` `sqlite_file`, `name` the
    absolute path.
  - A side file that does not exist is not listed. Other files (`other.sqlite3-wal`,
    `<copy>-wal.bak`, `<copy>.backup`) are not touched.
  - Listing only (for `--dry-run`) returns the same items and deletes nothing.
  - A recorded copy that is already gone → already absent; its side files are left alone
    (FR-039: never on their own; reading R2).
  - Postgres: the password comes from the resolved `wtenv.toml` pattern when there is one,
    otherwise from libpq (`PGPASSWORD`, `PGPASSFILE`, `~/.pgpass`); a failed connection is
    returned as a failure reason that never contains the password (FR-019).
- [X] T088 [US4] Implement SQLite removal with side files in `src/wtenv/database.py`
- [X] T089 [US4] Implement Postgres removal in `src/wtenv/database.py`
  - `DROP DATABASE IF EXISTS <name> WITH (FORCE)` from the `postgres` database (research.md
    §3); a missing database is already absent (FR-042); the template is never touched (FR-027).

### 6C. Removing a compose project (`compose.py`)

- [X] T090 [P] [US4] Write failing tests for compose project removal in `tests/unit/test_compose_teardown.py`
  - With a fake command runner: list containers, networks, and volumes labelled
    `com.docker.compose.project=<project>` before and after
    `docker compose -p <project> down --volumes --remove-orphans`, run from a directory with
    no compose file (research.md §4).
  - Each removed resource is an item (`compose_container`, `compose_network`,
    `compose_volume`); a project with nothing left is already absent.
  - Labelled resources still there are removed with `docker rm`, `docker network rm`,
    `docker volume rm`; what remains is failed.
  - Only resources with the recorded project's label are ever named in a removal command;
    external volumes and networks (no label) never are (FR-039). Listing only runs no removal.
- [X] T091 [US4] Implement compose project listing and removal in `src/wtenv/compose.py`

### 6D. `wtenv down` (`teardown.py`, `provision.py`, `cli.py`, `output.py`)

- [X] T092 [P] [US4] Write failing integration tests for basic `down` and `--dry-run` in `tests/integration/test_us4_lifecycle.py`
  - Scenario 1 (FR-038, FR-041): `down` removes wtenv's section (and the env file when wtenv
    created it and nothing else is in it), releases the block, removes the entry, and lists
    each item under `removed`.
  - Scenario 2 (FR-040): `down --dry-run` changes nothing (registry and files
    byte-identical) and lists the same items under `would_remove`.
  - Scenario 7 (FR-043): `down` in a never-provisioned worktree exits 0 and changes nothing;
    `down` twice is harmless. From a subdirectory works; outside a worktree → exit 4 (FR-002).
  - FR-079: the line break wtenv added is removed; the developer's lines match the bytes
    before the first `up`. `DownResult` validates with `worktree_path` set.
- [X] T093 [US4] Add failing tests for `down` errors and partial failure to `tests/integration/test_us4_lifecycle.py`
  - FR-044: `down` works with no `wtenv.toml`; an invalid one gives warning
    `config_ignored`; an env file deleted by hand is under `already_absent` (FR-042).
  - FR-081: damaged markers → section under `failed`, file unchanged, entry `incomplete`,
    exit 13; after repair, `down` finishes (FR-042).
- [X] T094 [US4] Add failing tests for `down` with the exclude block and a moved worktree to `tests/integration/test_us4_lifecycle.py`
  - FR-085: with two worktrees of one repository, the first `down` keeps the exclude block,
    the second removes it, markers included, keeping the developer's lines.
  - FR-084: `down` after `git worktree move` releases, records the new location, and warns
    `worktree_moved` (cli.md, `wtenv down`, Warnings).
- [X] T095 [US4] Add failing tests for `down` with SQLite side files to `tests/integration/test_us4_lifecycle.py`
  - FR-039: with `-wal`, `-shm`, and `-journal` beside the recorded copy,
    `down --dry-run --json` lists the copy and each side file as separate `would_remove`
    items (`kind` `sqlite_file`, `name` the absolute path) and deletes nothing; `down --json`
    lists the same items under `removed` and they are gone; a missing side file is not
    listed. US2 scenario 5 for SQLite: the template and another worktree's copy are unchanged.
- [X] T096 [P] [US4] Write failing decoy tests for `down` in `tests/integration/test_safety_decoys.py`
  - SC-007, FR-039: an unrecorded `<worktree>/.wtenv/decoy.sqlite3` with `-wal` and
    `-journal` files, and a wtenv-marked section in a file that is not the recorded env file,
    are still there after `down`.
  - With `docker`: database `wtenv_decoy_00000000`, a volume and a container labelled
    `com.docker.compose.project=wtenv-decoy-00000000` survive `down`.
- [X] T097 [US4] Add failing Docker-backed `down` tests to `tests/integration/test_us4_lifecycle.py`
  - US2 scenario 5 (Postgres): `down` drops the worktree's database, also while a session is
    connected to it (spec Edge Cases), and leaves the template and other databases.
  - No password available (none in `wtenv.toml`, no `PGPASSWORD`) → database under `failed`,
    still recorded, exit 13; with the password, `down` finishes.
  - Compose: after `docker compose up -d`, `down` removes the project's containers, networks,
    named volumes, and the override, listing each; an `external` volume is kept.
  - Items come out in the order of cli.md, `wtenv down`.
- [X] T098 [US4] Add failing recovery tests for `down` to `tests/integration/test_recovery.py`
  - `down` interrupted (as in T041) after a resource is marked `removing` and before it is
    removed, and after it is removed and before its record is dropped: `down` again finishes
    and reports what was gone under `already_absent` (FR-069).
  - After an interrupted `down`, `up` restores the worktree: a `removing` resource that still
    exists is kept and marked `created`; one that is gone is created again (data-model.md,
    Resource states).
  - With Postgres: a database the server marks invalid (simulate with
    `UPDATE pg_database SET datconnlimit = -2`, PostgreSQL's mark for an interrupted drop)
    → `up` exits 19, reason `interrupted_removal`, hint naming `wtenv down`.
- [X] T099 [US4] Add a failing test for `up` and `down` at once to `tests/integration/test_concurrency.py`
  - Both started together in one worktree run one after the other; the end state is that of
    the two in lock order (spec Edge Cases).
- [X] T100 [US4] Implement the release plan in `src/wtenv/teardown.py`
  - From the entry alone (FR-044), the items a release would remove, in cli.md's order:
    compose containers, networks, volumes, the override; databases (a SQLite copy followed
    by its existing side files); the env section (and the file when it would be deleted);
    the port block; the registry entry; the exclude entries when this is the repository's
    last entry (FR-085). This is the `--dry-run` output; no worktree lock, no change.
- [X] T101 [US4] Implement releasing one entry in `src/wtenv/teardown.py`
  - Per resource: mark it `removing`, remove it, drop its record; the registry lock only for
    those short updates (FR-068).
  - Each item ends `removed`, `already_absent`, or `failed` with a reason (FR-041). Failed
    items stay recorded, the entry stays `incomplete`, the caller exits 13 (FR-042).
  - When nothing is left: delete the entry and its block in one transaction, and remove the
    exclude block if no other entry of that repository remains (FR-085). Shared by `down`
    and `gc`; removes only what the entry records (FR-039).
- [X] T102 [US4] Handle resources recorded as `removing` in `up` in `src/wtenv/provision.py`
  - data-model.md, Resource states, column "Next `up`": still there and usable → keep, mark
    `created`; gone → create again; an invalid Postgres database → `unsupported`, reason
    `interrupted_removal`.
- [X] T103 [US4] Add `wtenv down [--dry-run] [--json]` to `src/wtenv/cli.py` and its text output to `src/wtenv/output.py`
  - Identify the worktree; without `--dry-run` take the worktree lock (60 s); record a new
    location and warn `worktree_moved` if it moved (FR-084); release. No entry → exit 0,
    nothing removed (FR-043). Exit statuses 0, 4, 13, 14, 15, 16.

### 6E. `wtenv gc` (`orphans.py`, `cli.py`, `output.py`)

- [X] T104 [US4] Add failing tests for plain `gc` releasing orphans (scenarios 3–5) to `tests/integration/test_us4_lifecycle.py`
  - Scenario 3 (FR-045): entries of worktrees removed with `git worktree remove` are
    released; items under `removed`, paths under `released`.
  - Scenario 4 (FR-075): `gc --dry-run` changes nothing and fills `would_release` and
    `would_remove`.
  - Scenario 5 (FR-046, FR-047): with existing, orphaned, and unverifiable entries, only the
    orphaned are released; existing worktrees are not listed.
  - FR-085: when `gc` releases the last entry of a repository, the exclude block is removed,
    markers included, keeping the developer's lines; while another entry of that repository
    remains, the block is kept.
  - Works outside any repository (FR-003); a second run behaves the same (FR-075).
- [X] T105 [US4] Add failing tests for plain `gc` keeping and skipping entries (scenario 8) to `tests/integration/test_us4_lifecycle.py`
  - Scenario 8 (FR-072): under `kept` with reason, nothing removed, exit 0: directory deleted
    by hand (`git_still_lists`); repository deleted (`repository_not_found`); moved
    (`moved`, `current_path`); something at a removed worktree's path (`path_exists`).
  - After `git worktree prune` the hand-deleted one is released; `gc` itself never prunes:
    git still lists a prunable worktree after `gc` (FR-075).
  - An entry whose worktree lock is held → `skipped_busy`, exit 0 (FR-077).
  - FR-074: a worktree that reappears between the first classification and the release is
    skipped (in-process, second classification patched).
- [X] T106 [US4] Add failing tests for `gc --release` to `tests/integration/test_us4_lifecycle.py`
  - Scenario 9 (FR-073): after the repository is deleted, `gc --release <path>` releases
    the entry and lists each item; a second run reports the path under `no_entry`, exit 0.
  - Only the named entries are acted on; an unnamed orphan stays.
  - A path whose worktree still exists → exit 18 `worktree_exists` with `details.path`; a
    moved worktree that exists elsewhere → exit 18 with `details.current_path`; nothing
    changed for any path.
  - `--release PATH --dry-run` lists and changes nothing.
  - SQLite side files (FR-039; reading R3): a linked worktree with a SQLite copy and side
    files; its repository deleted, the worktree directory left. `gc --release <path>
    --dry-run --json` lists the copy and each existing side file as separate `would_remove`
    items; without `--dry-run` they are under `removed` and gone.
- [X] T107 [US4] Add failing Docker-backed `gc` tests to `tests/integration/test_us4_lifecycle.py`
  - Request `docker`. An orphan's Postgres database without a password → `failed`, still
    recorded, exit 13; with `PGPASSWORD`, the next `gc` removes it (cli.md, Credentials).
  - SC-003: with Postgres and a started compose stack, `git worktree remove --force` then one
    `gc` leaves no database, container, volume, or entry of it.
- [X] T108 [US4] Add `gc` decoy tests to `tests/integration/test_safety_decoys.py`
  - The decoys of T096 survive plain `gc` and `gc --release` of a neighbouring entry (SC-007).
- [X] T109 [US4] Implement plain `gc` in `src/wtenv/orphans.py`
  - Read the registry once; one `git --git-dir=<repository> worktree list --porcelain` per
    repository; classify every entry.
  - Each `orphaned` entry: try its worktree lock without waiting (held → `skipped_busy`);
    holding it, classify again with a fresh listing and skip the entry if it is no longer
    orphaned (FR-074); release through `teardown`.
  - `unverifiable` → `kept`. `kept` and `skipped_busy` never change the exit status; a
    failed item gives 13. `--dry-run`: classify and list through the release plan, no lock.
  - Never run a git command that changes a repository (FR-075).
- [X] T110 [US4] Implement `gc --release PATH` in `src/wtenv/orphans.py`
  - Check every `PATH` first: an entry whose worktree still exists there, or (reason
    `moved`) elsewhere → `worktree_exists`, nothing changed. A `PATH` with no entry →
    `no_entry`. Then release each named entry with the plain `gc` lock rule (cli.md).
- [X] T111 [US4] Add `wtenv gc [--dry-run] [--release PATH]... [--json]` to `src/wtenv/cli.py` and its text output to `src/wtenv/output.py`
  - `GcResult`; exit statuses 0, 13, 14, 16, 18.
- [X] T149 [US4] Add failing tests for `gc --release` refusing a path where a live worktree is, to `tests/integration/test_us4_lifecycle.py`
  - Reading R5: the repository is moved and `git worktree repair` points the worktree at its new
    git directory; `gc --release <path>` for the old entry → exit 18 `worktree_exists` with
    `details.path`; the registry and the env file are byte-identical.
  - A worktree of another repository at a removed worktree's recorded path → the same.
- [X] T150 [US4] Refuse in `gc --release` a path whose `.git` names an existing git directory, in `src/wtenv/orphans.py`
  - Reading R5 (cli.md, `gc --release`, step 1); found by the review of the destructive paths.

### 6F. `wtenv ls` (`listing.py`, `cli.py`, `output.py`)

- [X] T112 [US4] Add failing tests for `ls` to `tests/integration/test_us4_lifecycle.py`
  - Scenario 6 (FR-048, FR-049): entries of two repositories listed from anywhere, each with
    repository, path, block, each variable's port, published ports, database, compose
    project, and status.
  - Inside a repository, its worktrees without an entry are `unprovisioned`; outside any
    repository `ls` still works (FR-003).
  - Statuses: `provisioned`; `incomplete` after a failed `up`; `orphaned` after
    `git worktree remove` (FR-054); `unverifiable` with `reason`, and `current_path` when
    moved.
  - FR-050: the registry is byte-identical afterwards; `ls` works while another process
    holds a worktree lock (FR-068, FR-076).
  - `LsResult` validates; the text table has cli.md's columns; no credentials (FR-019).
- [X] T113 [P] [US4] Add an `ls --json` case to `tests/unit/test_lazy_imports.py`
  - After `wtenv.cli.main(["ls", "--json"])` in a fresh interpreter, `psycopg` and
    `wtenv.compose` are not in `sys.modules` (NFR-001; research.md §8).
- [X] T114 [US4] Implement the `ls` views in `src/wtenv/listing.py`
  - One `WorktreeView` per entry, status from `classify`. Inside a repository, add the
    worktrees that are in the listing, exist on disk, and whose `points_to` is not a registry
    key, as `unprovisioned` (data-model.md).
- [X] T115 [US4] Add `wtenv ls [--json]` to `src/wtenv/cli.py` and its table to `src/wtenv/output.py`
  - Registry lock only, briefly; no worktree lock (FR-076). Exit statuses 0, 14, 16.

**Checkpoint**: US1–US4 work; the five gates pass.

### 6G. Symbolic links in `down` and `gc` (`teardown.py`) — FR-086

Added after the review of the destructive paths (2026-10-05, finding MEDIUM-1). Run this
group after Phase 7 and group 3F (it uses T153's `symlinked_part`).

- [X] T155 [US4] Add failing tests for `down` and symbolic links to `tests/integration/test_us4_lifecycle.py`
  - FR-086: after `up`, `.env.local` replaced by a link to a file that holds a wtenv
    section → `down` lists the section under `failed` with `reason` `symlink`; the link and
    its target byte-identical; the entry `incomplete`, its block still recorded; exit 13.
    After the link is replaced by a regular file, `down` finishes (FR-042).
  - SQLite: `.wtenv` replaced by a link to another worktree's `.wtenv/` holding a copy and
    a `-wal` file → the copy under `failed`, `reason` `symlink`; no side-file item; the other
    worktree's files byte-identical; exit 13.
  - `down --dry-run`: the same items under `failed`, not under `would_remove`; nothing
    changes; exit 0.
  - With `docker`: the override replaced by a link → the override under `failed`, `reason`
    `symlink`, the target unchanged, exit 13.
- [X] T156 [US4] Add failing tests for `gc --release` and symbolic links to `tests/integration/test_us4_lifecycle.py`
  - FR-086: a linked worktree's repository deleted, the worktree directory left, its
    `.env.local` a link to a shared file → `gc --release <path>`: the section under
    `failed` with `reason` `symlink`, the path not under `released`, the entry still
    recorded, the shared file byte-identical, exit 13. With `--dry-run`: under `failed`, not
    under `would_release`, exit 0.
  - A plain `gc` needs no case: an orphan's path does not exist (FR-045, check 3).
- [X] T157 [US4] Leave symbolic links alone when releasing an entry in `src/wtenv/teardown.py`
  - FR-086: before the override, the SQLite copy, or the env section is touched (before it
    is marked `removing`), check its path with `identity.symlinked_part`. A link →
    `FailedItem` with `reason` `symlink`; for the SQLite copy, side files are not looked
    for. Shared by `down`, `gc`, and `gc --release`, so a dry run lists the same items.

---

## Phase 7: User Story 5 - Auto-provisioning and exec (Priority: P5)

**Goal**: an opt-in git `post-checkout` hook runs `wtenv up` in new worktrees, and
`wtenv exec -- <command>` runs a command with the worktree's variables. The Claude Code
integration (FR-055, US5 scenario 4) is not delivered; it has no tasks.

**Independent Test**: install the hook, run `git worktree add`: the new worktree is
provisioned. `wtenv exec -- env` shows the worktree's variables. Automated by T118 and T121.

### 7A. The hook block (`hooks.py`)

- [X] T116 [P] [US5] Write failing tests for the hook block in `tests/unit/test_hooks_block.py`
  - The block is the text in files.md, "Git hook block", byte for byte; its `wtenv up` line
    ends in `|| true`, so the hook's exit status is never changed (FR-052).
  - Insert directly after the shebang of an existing POSIX shell hook (reading R4); a new
    file gets `#!/bin/sh`; inserting again changes no byte; an outdated block is rewritten in
    place.
  - Remove takes out exactly the block; every other byte stays.
  - A hook that is not a POSIX shell script → `unsupported`, reason `hook_not_shell`, with
    the block in the hint. Damaged markers → reason `markers_damaged`.
- [X] T117 [US5] Implement the block text, insertion, and removal in `src/wtenv/hooks.py`

### 7B. `wtenv hook install` and `wtenv hook uninstall`

- [X] T118 [P] [US5] Write failing integration tests for the hook in `tests/integration/test_us5_hook_exec.py`
  - Setup: `PATH` starts with the directory of the test environment's `wtenv` script
    (`Path(sys.executable).parent`).
  - Scenario 1 (FR-051): after `hook install`, `git worktree add` with a branch and with
    `--detach` leaves the new worktree provisioned.
  - Scenario 2 (FR-052), the hook always lets git exit 0: a `wtenv.toml` that makes `up`
    fail → `git worktree add` exits 0, the worktree exists, wtenv's error is on stderr;
    `wtenv` missing from `PATH` → exit 0 with the not-found message.
  - Scenario 3 (FR-051): `git switch -c`, `git checkout HEAD -- <file>`, and `git clone`
    provision nothing (registry unchanged).
  - Scenario 7 (FR-053): an existing hook keeps running its own lines after install, and is
    byte-identical after uninstall.
  - A hook file wtenv created is deleted by uninstall when only the shebang is left;
    `hook uninstall --dry-run` lists `hook_block` (and `hook_file`) under `would_remove`
    and changes nothing (FR-040); no block → `action` `absent`.
  - Installing twice → `action` `unchanged`; `up`, `down`, `gc`, `ls`, and `doctor` never
    install it (FR-051).
  - `core.hooksPath` elsewhere → exit 19, reason `hooks_path_redirected`, block in the hint,
    nothing written; a non-shell hook → reason `hook_not_shell`.
  - `hook install` and `hook uninstall` outside any worktree → exit 4 `not_in_worktree`,
    nothing written (cli.md, Where it runs).
- [X] T119 [US5] Implement install and uninstall in `src/wtenv/hooks.py`
  - Hook file `<git-common-dir>/hooks/post-checkout`; `git rev-parse --git-path hooks` must
    equal `<git-common-dir>/hooks`, otherwise `hooks_path_redirected` (research.md §1).
  - A new file gets mode `0755` and a `HookRecord` with `created_file` true; uninstall
    deletes the file only then, and only when nothing but the shebang is left.
- [X] T120 [US5] Add `wtenv hook install` and `wtenv hook uninstall [--dry-run]` to `src/wtenv/cli.py` and their output to `src/wtenv/output.py`
  - `HookInstallResult`, `HookUninstallResult`; exit statuses 0, 4, 14, 16, 19.

### 7C. `wtenv exec` (`execcmd.py`, `cli.py`)

- [X] T121 [US5] Add failing tests for `exec` to `tests/integration/test_us5_hook_exec.py`
  - Scenario 5 (FR-056): the command sees every variable in wtenv's section of the env file;
    standard input, output, and error pass through; wtenv exits with the command's status
    (for example 7). The developer's own env-file lines are not loaded.
  - Reading R1, each case checked against the env file, not the registry:
    - with a SQLite database configured in a worktree whose path contains a space (so the
      value is written in single quotes), `DATABASE_URL` equals the section's value with
      its quotes removed, and each port variable equals its section value;
    - a value edited by hand inside the section (for example `PORT=29999`) reaches the
      command as written;
    - damaged markers → exit 125, `error.code` `env_file_unusable`, reason
      `markers_damaged`, command not run;
    - env file deleted → exit 125, `error.code` `env_file_unusable`, reason `missing`,
      command not run;
    - env file present but with no wtenv section (both marker lines and the lines between
      them deleted by hand) → exit 125, `error.code` `env_file_unusable`, reason
      `no_section`, command not run;
    - `[database]` removed from `wtenv.toml` and `up` run again (the database stays
      recorded) → the command's environment has no `DATABASE_URL`.
  - Scenario 6 (FR-057): unprovisioned or incomplete → exit 125, command not run; with
    `--json`, an `ExecResult` with `error.code` `not_provisioned`, `error.exit_status` 125,
    and `details.status`.
  - A missing `--` or command → usage error, exit 125. Command not found → 127; found but
    not executable → 126 (cli.md, `wtenv exec`).
  - When the command runs, wtenv prints nothing. `exec` takes no worktree lock (FR-076).
- [X] T122 [US5] Implement `exec` in `src/wtenv/execcmd.py`
  - Status from `classify`; only `provisioned` runs. Environment: the current one plus every
    variable in wtenv's section of the recorded env file (`EnvFileRecord.path`), ports
    included, read and unquoted with `envfile.read_section` (reading R1). `wtenv.toml` is not
    read; the registry supplies only the path. A missing file, a file with no section, or
    damaged markers → `env_file_unusable` (reason `missing`, `no_section`, or
    `markers_damaged`), exit 125. Replace the process with `os.execvpe`, so signals reach
    the command directly.
- [X] T123 [US5] Add `wtenv exec [--json] -- COMMAND [ARG]...` to `src/wtenv/cli.py`
  - `--` is required; any wtenv failure in `exec` exits 125 with the real code in
    `error.code` (cli.md).

**Checkpoint**: US1–US5 work; the five gates pass.

---

## Phase 8: User Story 6 - Diagnostics (Priority: P6)

**Goal**: `wtenv doctor` reports port conflicts, stale entries, and missing dependencies,
and the `--json` contract is checked for every command.

**Independent Test**: run every command with `--json` and parse stdout as one document;
create each problem `doctor` checks for and confirm its code, and that `doctor` changed
nothing. Automated by T124–T125 and T130–T134.

### 8A. `wtenv doctor` (`doctor.py`, `cli.py`, `output.py`)

- [X] T124 [P] [US6] Write failing tests for the doctor checks in `tests/unit/test_doctor_checks.py`
  - Findings (cli.md, `wtenv doctor`): `block_overlap` (FR-060a); `orphaned_worktree`,
    `unverifiable_worktree` with `details.reason`, `incomplete_worktree`, from `classify`
    (FR-060c, d; FR-054); `missing_resource` for a recorded env section, env file, SQLite
    copy, override, or Postgres database (on a reachable server) that no longer exists;
    `resource_check_skipped` (info) when the server cannot be reached.
  - Ports (FR-060b; research.md §11): a busy assigned port belongs to the worktree when a
    container of its compose project publishes it, or `lsof` shows the listener's working
    directory inside the worktree; anything else → `port_conflict`; `lsof` missing or unsure
    → `port_in_use` (info).
  - Dependencies (FR-060e): `git`, `docker`, `postgres` each `ok`, `unavailable`, or
    `not_required`; not needed by the current worktree's `wtenv.toml` → `not_required`,
    never a finding; outside a repository `docker` and `postgres` are `not_required`; needed
    and unavailable → `dependency_unavailable`.
  - Exit 0 without `problem` findings; otherwise 17, `ok` false, `error.code`
    `problems_found`, `details.problems` the count (FR-061).
  - The checks take the command runner as a parameter (`lsof`, `docker`).
- [X] T125 [P] [US6] Write failing integration tests for `doctor` in `tests/integration/test_us6_diagnostics.py`
  - Scenario 3: a healthy setup → exit 0, no `problem` finding.
  - Scenario 4: a listener started outside the worktree on an assigned port →
    `port_conflict`; an entry whose worktree was removed with git → `orphaned_worktree`; a
    `wtenv.toml` needing Postgres on a closed local port → `dependency_unavailable`; exit 17;
    registry and files byte-identical afterwards (FR-060).
  - A listener started inside the worktree is not a conflict.
  - Works outside any repository (FR-003); takes no worktree lock (FR-076); `DoctorResult`
    validates.
- [X] T126 [US6] Implement the registry checks in `src/wtenv/doctor.py`
- [X] T127 [US6] Implement the port-holder checks in `src/wtenv/doctor.py`
- [X] T128 [US6] Implement the dependency checks in `src/wtenv/doctor.py`
- [X] T129 [US6] Add `wtenv doctor [--json]` to `src/wtenv/cli.py` and its output to `src/wtenv/output.py`
  - Exit statuses 0, 14, 16, 17.

### 8B. The agent contract for every command

- [X] T130 [P] [US6] Write contract tests for the command surface in `tests/contract/test_command_surface.py`
  - The commands and options are exactly cli.md's synopsis, nothing more (FR-001).
- [X] T131 [P] [US6] Write contract tests for every `--json` document in `tests/contract/test_json_documents.py`
  - The module creates worktrees, so it sets `pytestmark = pytest.mark.integration` and runs
    in the integration gate.
  - For `--version`, `up`, `down`, `down --dry-run`, `gc`, `gc --dry-run`, `gc --release`,
    `ls`, `doctor`, `hook install`, `hook uninstall`, `hook uninstall --dry-run`, and a
    failing `exec`: stdout is exactly one document that validates against the matching
    model of `json_models.py`; everything else is on stderr; `ok` is true exactly when the
    exit status is 0 (FR-058; US6 scenarios 1–2; SC-008).
- [X] T132 [P] [US6] Write contract tests for the exit statuses that need no git worktree in `tests/contract/test_exit_statuses.py`
  - The module sets `pytestmark = pytest.mark.integration` (later cases create worktrees).
  - One triggering case per code, checking code and status (FR-059): 1, 2, 4 (outside any
    repository), 16 (a registry file that is not JSON, with `ls`).
- [X] T133 [US6] Add contract tests for the exit statuses of `up` to `tests/contract/test_exit_statuses.py`
  - 3, 6, 7, 8 (Postgres on a closed port), 9 and 10 and 11 (SQLite), 12.
- [X] T134 [US6] Add contract tests for the exit statuses of the other commands to `tests/contract/test_exit_statuses.py`
  - 13 (damaged markers on `down`), 17, 18, 19 (`core.hooksPath`); 14 and 15 through the
    in-process functions with small bounds; 5 inside `exec`'s 125.
- [X] T135 [US6] Run `tests/contract/` and correct each deviation in `src/wtenv/cli.py` or `src/wtenv/output.py`
  - Done when every test in `tests/contract/` passes, in both pytest gates.

**Checkpoint**: all six stories work; the five gates pass.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: performance, coverage, CI, release, documentation, and the end-to-end run.

**Order**: part 1, the findings of the first review of the destructive paths (T158–T173);
then group 9S, the safety fixes from the second and third reviews (T174–T222); then part 2, polish,
CI, and release (T136–T148). Part 2 runs after group 9S.

### Part 1: findings of the review of the destructive paths (2026-10-05)

Findings LOW-1, LOW-2, and LOW-4 to LOW-7 of that review: each could remove or overwrite
something wtenv did not record, or hide an error. LOW-3 is on `docs/roadmap.md`. Run these
after groups 3F and 6G, and before T146–T148. Each pair is a failing test, then the code.

- [X] T158 Add failing tests for `gc --release` naming a path that is now a symbolic link to `tests/integration/test_us4_lifecycle.py`
  - LOW-1, FR-073, FR-086: an entry whose repository was deleted and whose recorded path was
    then replaced by a link to another directory → `gc --release <path>` finds the entry
    (not `no_entry`); its files are under `failed` with `reason` `symlink`; nothing in the
    link's target changes; exit 13. A link to a live worktree → exit 18 (reading R5).
- [X] T159 Match `--release PATH` as given before resolving it, in `src/wtenv/orphans.py`
  - cli.md, `gc --release`, step 1: the entry recorded at `os.path.abspath(PATH)`, else the
    one at `os.path.realpath(PATH)`.
- [X] T160 Add failing tests for a dry run that cannot reach Postgres or Docker to `tests/unit/test_database_remove.py` and `tests/unit/test_compose_teardown.py`
  - LOW-2, FR-040: with the server or the engine unreachable, the dry-run removal returns
    the item as failed, with a reason naming the dependency, and not as would-be removed.
  - Integration, in `tests/integration/test_us4_lifecycle.py`: an entry whose recorded
    Postgres port has nothing listening → `down --dry-run --json` lists the database under
    `failed` and leaves the port block and the entry out of `would_remove`; exit 0; and
    `gc --release <path> --dry-run` leaves the path out of `would_release`.
- [X] T161 List unreachable items as failed in dry runs, in `src/wtenv/database.py` and `src/wtenv/compose.py`
  - cli.md, `wtenv down` and `wtenv gc`, `--dry-run`. `teardown` already leaves the block and
    the entry out when an item failed.
- [X] T172 Add failing contract tests for `KeptVolume` and `kept_volumes` to `tests/contract/test_models_match_contract.py`
  - Reading R6, FR-041, FR-058: as T009, `wtenv.output.KeptVolume` exists and its
    `model_json_schema()` equals the contract's; `DownResult` and `GcResult` have a
    `kept_volumes` field, default empty, and their schemas equal the contract's.
- [X] T173 Add `KeptVolume` and the `kept_volumes` field of `DownResult` and `GcResult` to `src/wtenv/output.py`
  - Port them from `json_models.py` as T011 did; `KeptEntry` and `kept` are unchanged.
    T172 passes. Comes before T162 and T163.
- [X] T162 Add a failing Docker-backed test for volumes to `tests/integration/test_us4_lifecycle.py`
  - LOW-4, FR-039, FR-040, FR-041, reading R6: the test's own compose project has three
    volumes: one named volume (`named:/named`; Compose labels it
    `com.docker.compose.project=<project>`), one anonymous volume (a service with a `/data`
    mount; Docker labels it only `com.docker.volume.anonymous`), and one external decoy
    (created with `docker volume create`, declared `external: true`, mounted by a service;
    no label). The project is started with `docker compose up -d`, so all three are
    attached to a container of that project.
  - `down --dry-run --json`: the named volume is its own `compose_volume` item in
    `would_remove`; the anonymous volume and the decoy are in `kept_volumes`, each with
    `name` the volume's name, `project` the recorded project name, and `reason`
    `unlabelled`; all three volumes are still there afterwards.
  - `down --json`: the named volume is in `removed` and gone together with the project. The
    anonymous volume and the decoy are in `kept_volumes` as above and still exist, with
    their contents unchanged (a file written into each before `down` is read back after it).
    The same cases through `gc` (after the worktree is deleted) and `gc --release` (after the
    repository is deleted), in `GcResult.kept_volumes`, with and without `--dry-run`.
  - `docker compose down` is run without `--volumes`, and no `docker volume rm` names an
    unlabelled volume (assert on the commands, in `tests/unit/test_compose_teardown.py`,
    with a fake engine that holds labelled, anonymous, and external volumes).
  - The test removes every container, network, and volume it created, including the kept
    ones; it touches nothing else.
- [X] T163 Remove labelled volumes by name instead of `--volumes`, in `src/wtenv/compose.py`
  - Reading R6 (accepted, amended 2026-10-05 after a probe on Docker 29.5.3 and Compose
    5.1.4: only named volumes carry `com.docker.compose.project`; anonymous and external
    volumes do not). Drop `--volumes` from `docker compose down`.
  - Removal: list the volumes labelled `com.docker.compose.project=<recorded project name>`
    and remove each by name, as its own `compose_volume` item in `removed` (or
    `would_remove` with `--dry-run`). A volume without that label never reaches a removal
    command. External volumes are never removed (FR-039).
  - Reporting: before `docker compose down`, run `docker inspect` on the project's
    containers (found by the label) and collect the volumes they mount. Every mounted
    volume that does not carry the project label (anonymous or external) is reported in
    `kept_volumes` (`DownResult`, and `GcResult` through `gc`) as a `KeptVolume` with
    `name`, `project` the recorded project name, and `reason` `unlabelled`, once per volume
    and in name order. The same inspection runs for `--dry-run`. If Docker cannot be asked,
    the item is `failed` as for the other listings.
  - Known limit (cli.md, `wtenv down`): an anonymous volume of a removed project stays on
    disk. docs/roadmap.md has an entry for an opt-in cleanup.

- [X] T164 Add a failing Docker-backed test for a compose project that already exists to `tests/integration/test_us3_compose.py`
  - LOW-4, FR-024, FR-039: containers started with `docker compose -p <generated name>
    up -d` before the first `up` → `up` exits 11, `ownership_conflict`, `details.kind`
    `compose_project`; nothing recorded for compose; a later `down` leaves those containers.
- [X] T165 Refuse an existing, unrecorded compose project in the compose check of `up`, in `src/wtenv/compose.py` and `src/wtenv/provision.py`
  - data-model.md, Resource states: before recording `creating`, nothing may exist at the
    name. Resources with the label `com.docker.compose.project=<name>` and no compose record
    → `ownership_conflict`. A record in state `creating` is an interrupted run and adopts
    them (FR-067).
- [X] T166 Add a failing test for `gc --release` on a moved entry with no known location to `tests/integration/test_us4_lifecycle.py`
  - LOW-5, FR-073: an entry classified `moved` with `current_path` None, whose
    `<git_dir>/gitdir` names a `.git` file that exists → exit 18, `details.current_path`
    that file's directory; nothing changed. If real git commands cannot produce this state,
    patch the classification in-process, as T105 does.
- [X] T167 Refuse such an entry in `_refuse_if_it_exists` in `src/wtenv/orphans.py`
- [X] T168 Add failing tests for the Postgres drop guard to `tests/unit/test_database_remove.py`
  - LOW-6, FR-025, FR-039: a recorded name that does not match
    `^wtenv_[a-z0-9_]{1,40}_[0-9a-f]{8}$` (files.md, Names), or a recorded host that is not
    local → failed item, no connection made, nothing dropped.
- [X] T169 Check the recorded name and host before a drop, in `src/wtenv/database.py`
- [X] T170 Add a failing test for a worktree that appears at a `--release` path during `gc` to `tests/integration/test_us4_lifecycle.py`
  - LOW-7, FR-073, FR-074: in-process, with a worktree created at the named path after the
    step-1 check and before the release (patched as in T105) → that entry is stopped with
    `worktree_exists`, exit 18, `details.path`; its files, database, and registry entry are
    unchanged (reading R7).
- [X] T171 Re-check that the worktree is still gone before each delete, in `src/wtenv/orphans.py`
  - Reading R7 (accepted): under the worktree lock, immediately before each delete of an
    entry, repeat the step-1 check (`_refuse_if_it_exists`) on the re-read entry. If the
    worktree has reappeared, stop that entry with `worktree_exists`, exit 18.

### 9S. Safety fixes from review 2 (2026-10-06)

The second review of the destructive paths, run after T158–T173, found two ways a
hand-edited registry makes `down` or `gc` delete something wtenv never created (H1, H2),
three medium findings (M1–M3), and eight low ones (L1–L8). The maintainer took M1 and M2 as
readings R8 and R9. Run this group after the findings group above and before part 2 below.
Each pair is a failing test, then the code. The hand-edited-registry tests (T176–T178) come
first: they fail until the pairs they name are done, and T211 closes them.

**Contract**

- [X] T174 Add failing contract tests for readings R8 and R9 to `tests/contract/test_models_match_contract.py`
  - Reading R8, reading R9, FR-058: as T172, `KeptVolume.reason` accepts `fixed_name` and
    `unlabelled` and nothing else; `WarningCode.COMPOSE_FIXED_VOLUME_NAME` is
    `compose_fixed_volume_name`; `UnverifiableReason.PARENT_MISSING` is `parent_missing`;
    the schemas of `KeptVolume`, `WarningInfo`, `KeptEntry`, `WorktreeView`, `DownResult`,
    and `GcResult` equal the contract's. The existing schema comparisons already fail once
    `json_models.py` has the new members; this task adds the explicit member checks.
- [X] T175 Port the new members from `json_models.py` to `src/wtenv/output.py`
  - As T173. T174 and the existing contract tests pass. Comes before every task below that
    emits `fixed_name`, `compose_fixed_volume_name`, or `parent_missing`.

**Hand-edited registry**

Each test provisions a worktree with `up`, edits one recorded field in `registry.json` to
point at a decoy the test created outside wtenv, then runs `down --dry-run`, `down`, `gc`
(after `git worktree remove`), and `gc --release` (after the repository is deleted). In
every run the decoy is unchanged (same bytes, same containers, same database). A value of a
form wtenv never records is under `failed` with its record kept, the entry stays, and the
exit status is 13 (0 for a dry run). Every test removes its decoys.

- [X] T176 Write failing hand-edited-registry tests for recorded paths in `tests/integration/test_hand_edited_registry.py`
  - FR-039, FR-044, FR-086: SQLite only, no Docker. `env_file.path` set to a developer file
    in the worktree (`src/main.py`), to `../outside.env`, and to an absolute path;
    `databases[sqlite].path` set to `../../notes.db` (with `notes.db-wal` and
    `notes.db-shm` beside it), to an absolute path, and to `data/notes.db` inside the
    worktree. `src/main.py` has the form of a recorded path but holds no wtenv section, so
    it is reported under `already_absent` and left byte for byte, even with
    `created_file` true. Passes after T182.
- [X] T177 Write failing hand-edited-registry tests for the compose project and the override in `tests/integration/test_hand_edited_registry.py`
  - FR-028, FR-039, FR-040: Docker; skips with a clear message without it. The decoy is a
    project `decoy-<random>` started with `docker compose -p decoy-<random> up -d`: one
    container, one network, one labelled named volume holding a file. `compose.project` set
    to the decoy's name, and to `wtenv-<slug>-<id8 of another git directory>`: nothing of
    the decoy is listed or removed, and no `docker` command names it. `compose.override` set
    to `src/main.py`, to `../compose.override.yaml`, to an absolute path, and to a
    `compose.override.yaml` the developer wrote without wtenv's header: the file is
    unchanged. The override cases also through `up` with `[compose]` removed from
    `wtenv.toml`: exit 11, `ownership_conflict`, nothing changed. Passes after T180, T182,
    and T184.
- [X] T178 Write failing hand-edited-registry tests for the Postgres record and for volume names in `tests/integration/test_hand_edited_registry.py`
  - FR-025, FR-039, FR-070: Postgres; skips with a clear message without it. Decoy
    databases `precious` and `wtenv_other_<id8 of another git directory>`, each created by
    the test. `databases[postgres].name` set to each: not dropped; `host` set to a host
    that is not local: no connection is made. Passes after T208 (`precious` already passes
    since T169).
  - Volume names are not a recorded field: volumes are found by the recorded project's
    label (data-model.md, Compose record), so T177 covers them. A `volumes` list naming a
    decoy volume added by hand to the `compose` record makes the registry invalid: every
    command exits 16, `registry_unreadable`, and the decoy volume still exists (Docker;
    skips without it). This case passes today and guards the schema.

**H1. The compose project name**

- [X] T179 Add failing tests for the compose project guard to `tests/unit/test_compose_teardown.py`
  - H1, FR-028, FR-039: with a fake engine that records every command. A recorded project
    that does not match `^wtenv-[a-z0-9-]{1,40}-[0-9a-f]{8}$` in full, or whose last 8
    digits are not `short_id(entry.git_dir, 8)`, gives one `compose_project` item under
    `failed` with the reason "not a project name wtenv generates"; no `docker` command is
    run, not even a listing; the override file is kept; the compose record stays. The same
    with `dry_run=True`. A record of the right form for its own git directory is removed as
    before.
- [X] T180 Check the recorded project name before any Docker call, in `src/wtenv/compose.py` and `src/wtenv/teardown.py`
  - cli.md, `wtenv down`, "Recorded values". `teardown` passes the entry's git directory.
    The git directory is the registry key and never changes, so real records always pass.

**H2. Recorded paths and the override file**

- [X] T181 Add failing tests for the recorded path guard to `tests/unit/test_teardown_paths.py`
  - H2, FR-039, FR-086: for `env_file.path`, `databases[sqlite].path`, and
    `compose.override`, each of an absolute path, a path with a `..` part, and a path that
    `posixpath.normpath` changes (`./a`, `a//b`) is one item under `failed` with a reason
    naming the rule; nothing is unlinked or rewritten; the record stays. A SQLite path that
    is not exactly `.wtenv/<file name>` (`data/x.db`, `.wtenv/sub/x.db`) fails the same way,
    and its side files are neither listed nor touched. The same with `dry_run=True`.
- [X] T182 Check recorded paths before using them, in `src/wtenv/identity.py` and `src/wtenv/teardown.py`
  - One helper beside `symlinked_part`, used for all three paths: relative, unchanged by
    `normpath`, no `..` part (the rule of `config.py`'s `env_file` check). The SQLite form
    is checked in `teardown.py`.
- [X] T183 Add failing tests for the override name and header to `tests/unit/test_teardown_paths.py` and `tests/integration/test_us3_compose.py`
  - H2, FR-039: a recorded override whose file name
    is not one of the four in files.md, Names, or whose first line is not wtenv's header,
    is `failed` in `down` and `gc` and is not deleted. In `up` with `[compose]` removed, or
    with the compose file moved, the same recorded override makes `up` fail with
    `ownership_conflict` (`details.kind` `compose_override`, `name` the path) before step 8;
    nothing changes. The same for a recorded env file that fails T181's checks when
    `env_file` changed.
- [X] T184 Check the override's name and header before deleting it, in `src/wtenv/teardown.py`, `src/wtenv/provision.py`, and `src/wtenv/compose.py`
  - cli.md, `wtenv up`, step 4, and `wtenv down`, "Recorded values". `up` runs the checks
    with its link checks, before anything changes (provision.py's check of the old
    override and old env file).

**M1. Volumes with a fixed name (reading R8)**

- [X] T185 Add failing tests for fixed-name volumes to `tests/unit/test_compose_teardown.py` and `tests/integration/test_us4_lifecycle.py`
  - M1, reading R8, FR-039, FR-041: a fake engine holding labelled volumes `<project>_data`
    and `shared-pgdata`. `<project>_data` is removed and reported; `shared-pgdata` is in
    `kept_volumes` with reason `fixed_name` and is never named in a `docker volume rm`.
    The same with `dry_run=True`.
  - Docker (skips without it): the test's compose file declares a volume with
    `name: wtenv-test-shared-<random>`, mounted by a service; `docker compose up -d`; then
    `down --dry-run --json`, `down --json`, and `gc` after the worktree is removed: the
    volume is in `kept_volumes` with `fixed_name` and still holds the file written into
    it. The test removes the volume.
- [X] T186 Remove only volumes named `<project>_…`, in `src/wtenv/compose.py`
  - Reading R8; cli.md, `wtenv down`. Any other labelled volume goes to `kept_volumes`,
    reason `fixed_name`, once per volume and in name order with the unlabelled ones.
- [X] T187 Add failing tests for the `compose_fixed_volume_name` warning to `tests/unit/test_compose_model.py`
  - M1, reading R8: a resolved model whose top-level volume has `name` other than
    `<model name>_<key>` and is not `external` gives the warning with `details` `volume`
    and `name`; an external volume and a volume with the default name give none.
- [X] T188 Warn about fixed volume names in `parse_model`, in `src/wtenv/compose.py`
  - cli.md, `wtenv up`, Warnings. The warning reaches `UpResult.warnings` the same way as
    `compose_fixed_container_name`.

**M2. A missing parent directory (reading R9)**

- [X] T189 Add failing tests for `parent_missing` to `tests/unit/test_classify.py` and `tests/integration/test_us4_lifecycle.py`
  - M2, reading R9, FR-045, FR-046, FR-072: unit, an entry whose git directory is gone,
    which no listing names, and whose recorded path's parent directory is missing →
    `unverifiable`, `parent_missing`; the same entry with the parent present → `orphaned`.
    The earlier reasons are unchanged when the parent is also missing.
  - Integration, real git: a worktree under `<tmp>/drive/`, provisioned with SQLite;
    `<tmp>/drive` renamed, then `git worktree prune --expire now`. `gc` keeps the entry
    under `kept` with `parent_missing`, the copy is untouched, and `ls` shows the reason.
    `gc --release <path>` then releases it.
- [X] T190 Classify a missing parent as `parent_missing`, in `src/wtenv/orphans.py`
  - data-model.md, "Status and the orphan checks", step 4.3. `doctor` and `ls` show the
    reason without further change.

**M3. Errors in the middle of a release**

- [X] T191 Add failing tests for an `OSError` in the env and exclude steps to `tests/unit/test_teardown_errors.py`
  - M3, FR-041, FR-042: `os.unlink` or the atomic write patched to raise `PermissionError`
    for the env file, and the same for `.git/info/exclude` → the `env_section` (or
    `env_file`) item, and the `exclude_entries` item, are under `failed` with a reason
    naming the path and the error; the entry stays `incomplete`; `down` exits 13, not 1.
- [X] T192 Turn an `OSError` in a file step into a failed item, in `src/wtenv/teardown.py`
- [X] T193 Add failing tests for an error during one entry of `gc` to `tests/unit/test_teardown_errors.py`
  - M3, FR-041, FR-073: three orphaned entries, the second's release patched to raise
    `registry_busy`. Plain `gc` and `gc --release` print a `GcResult` whose `released` and
    `removed` hold the first entry, whose `error.code` is `registry_busy` with its exit
    status 14, and in which the third entry is not touched.
- [X] T194 Catch a `WtenvError` per entry in `gc` and `gc --release`, stop, and return the partial result, in `src/wtenv/orphans.py`
  - As is already done for `worktree_exists` (cli.md, `wtenv gc`).

**L1–L8**

- [X] T195 Add a failing test for the exclude item in a `gc` dry run to `tests/integration/test_us4_lifecycle.py`
  - L1, FR-040, FR-085: the last two entries of a repository, both orphaned. `gc --dry-run`
    lists the `exclude_entries` item once, and its items equal the real run's `removed`.
    The same with both named to `gc --release --dry-run`.
- [X] T196 Count the entries a dry run would release as gone when deciding the last entry, in `src/wtenv/teardown.py` and `src/wtenv/orphans.py`
  - Pass the git directories the dry run already plans to release completely to `_finish`
    and leave them out of `_is_last`.
- [X] T197 Add a failing test for the paths `gc --release` did not attempt to `tests/integration/test_us4_lifecycle.py`
  - L2, FR-041, FR-073: three named paths; the first has a failed item, the second's
    worktree reappears (patched as in T170). Exit 18; `error.details.not_attempted` is the
    third path; the message names it; the hint mentions `failed`; `failed[]` still holds
    the first entry's item.
- [X] T198 Report not-attempted paths and an earlier partial failure when `gc --release` stops, in `src/wtenv/orphans.py`
  - cli.md, `wtenv gc --release`, step 3, and "Error details".
- [X] T199 Add a failing test for `gc --release` on an entry whose git directory still exists to `tests/integration/test_us4_lifecycle.py`
  - L3, FR-046, FR-073: a worktree moved by hand (`mv`, no `git worktree repair`), so it
    classifies as `git_still_lists`. `gc --release <old path>` → exit 18,
    `worktree_exists`, a hint naming `git worktree repair` and `git worktree prune`;
    nothing changes. After `git worktree prune` the release succeeds. Existing tests that
    release an entry whose git directory still exists are changed to prune first.
- [X] T200 Refuse `--release` while the entry's git directory exists, in `_refuse_if_it_exists` in `src/wtenv/orphans.py`
  - cli.md, `wtenv gc --release`, step 1. The re-check of T171 includes it.
- [X] T201 Add a failing test for a worktree that appears at the path during the compose step to `tests/integration/test_us4_lifecycle.py`
  - L4, FR-073, FR-074: in-process, as T170, a worktree with a different git directory
    added at the recorded path (`git worktree add -f`), with `.wtenv/`, an env section, and
    an override, after the compose step and before the disk steps → every file item under
    `failed` with reason `worktree_exists`; the new worktree's files are unchanged; the
    entry stays; exit 13. For plain `gc` and `gc --release`.
- [X] T202 Read `points_to` again after the compose step in `gc`, in `src/wtenv/teardown.py`
  - cli.md, `wtenv gc`, step 4. Not for `down`, whose root is the current worktree.
- [X] T203 Add a failing test for a link above the root under `gc` to `tests/integration/test_us4_lifecycle.py`
  - L5, FR-086: the worktree's parent directory replaced, after the worktree is gone, by a
    link to another directory holding the same file names → `gc --release <path>` puts
    every file item under `failed` with reason `symlink`; the link's target is unchanged.
- [X] T204 Fail the file items when the recorded root does not resolve to itself, in `src/wtenv/teardown.py`
  - When `os.path.realpath(root) != str(root)`.
- [X] T205 Add failing tests for a linked `.git/info/exclude` to `tests/unit/test_exclude.py` and `tests/integration/test_us4_lifecycle.py`
  - L6, FR-018, FR-086: `info/exclude` is a link to a file outside the repository. `down`
    of the last entry → `exclude_entries` under `failed`, reason `symlink`; the target is
    unchanged; exit 13. `up` → exit 7, `env_file_unusable`, reason `symlink`, `path` the
    link, before anything changes.
- [X] T206 Check `symlinked_part(repository, "info/exclude")` before writing the exclude block, in `src/wtenv/exclude.py`, `src/wtenv/teardown.py`, and `src/wtenv/provision.py`
  - cli.md, `wtenv up`, step 4, and `wtenv down`, symbolic links.
- [X] T207 Add failing tests for another worktree's database name to `tests/unit/test_database_remove.py`
  - L7, FR-039: a recorded name of the right form whose `<id8>` is not that of the entry's
    git directory → failed item, no connection, nothing dropped; dry run too.
- [X] T208 Require the entry's own `<id8>` in the drop guard, in `src/wtenv/database.py`
  - Extends T169: `name.endswith("_" + short_id(entry.git_dir, 8))`.
- [X] T209 Add failing tests for a SQLite side file that is a link to `tests/unit/test_database_remove.py`
  - L8, FR-039, FR-086: `<copy>-wal` is a link to a file elsewhere → the copy and each side
    file are under `failed` with reason `symlink`; nothing is unlinked; the link's target
    is unchanged; the record stays. Dry run too.
- [X] T210 Check side files with `lstat` before removing the copy, in `src/wtenv/database.py`

**Closing the group**

- [X] T211 Run T176–T178 and make them pass
  - Done when every hand-edited-registry case passes with T180, T182, T184, and T208 in
    place. A field still found unguarded is not fixed here: stop and ask, and add a task
    pair for it.

**Found after the close**

- [X] T212 Add failing tests for `up` over a changed override to `tests/unit/test_compose_checks.py` and `tests/integration/test_us3_compose.py`
  - FR-087: a temp repository and a temp state directory. The recorded override replaced by
    developer content with no header → `up` exits 11, `ownership_conflict`,
    `details.kind` `compose_override`, `details.name` the override path; the file is
    byte-identical afterwards and the registry is unchanged. The header line edited → the
    same. A stale file that still starts with the header → rewritten with the current text.
    A recorded override that is missing → created again.
- [X] T213 Check that a recorded override that exists starts with wtenv's header before `up` rewrites it, in `src/wtenv/compose.py` and `src/wtenv/provision.py`
  - FR-087, cli.md, `wtenv up`, step 5. Runs in `_compose_plan`, before anything changes.
    Reuses `override_problem`. After it, mutation-check "`up` never overwrites a file
    without wtenv's header": break the check, see T212 fail, restore it byte for byte.

- [X] T214 Add failing tests for `up` over a hand-edited recorded value to `tests/unit/test_recorded_value_checks.py` and `tests/integration/test_up_recorded_values.py`
  - FR-088: a temp repository and a temp state directory, never the real registry and never
    an existing Docker resource. Each test hand-edits one recorded field of a provisioned
    entry to point at a decoy and runs `up`: the compose project set to the main checkout's
    project (a name with no `-<id8>` of this entry's git directory); the Postgres name set
    to another worktree's name; the Postgres host set to a remote host; the SQLite path set
    to a path outside `.wtenv/` (`../main/db.sqlite3`, an absolute path, `data/db.sqlite3`).
    Each → exit 11, `ownership_conflict`, `details.kind` the item kind, the reason naming the
    field; the registry file is byte-identical, no file is written (env file, override,
    exclude block), the decoy (the main checkout's file, a path outside `.wtenv/`) is
    untouched, and no Docker or Postgres call is made for the failing field. A recorded
    value that passes still gives an unchanged `up`.
- [X] T215 Check recorded compose project, Postgres database, and SQLite path before `up` reuses them, in `src/wtenv/provision.py`, `src/wtenv/compose.py`, `src/wtenv/database.py`, and `src/wtenv/identity.py`
  - FR-088, cli.md, `wtenv up`, steps 5 and 7; runs in `_compose_plan` and `_database_plan`,
    before anything changes. Reuse `compose.project_problem` and the name-plus-`<id8>` and
    local-host checks of `remove_postgres_database` (factor them out as a function both call;
    `down`'s behaviour does not change), and move `teardown._sqlite_form_reason` to
    `identity` so both call it. After it, mutation-check "`up` never reuses a recorded value
    that fails the `down` checks": break the code, see T214 fail, restore it byte for byte.
- [X] T216 Add failing tests for the override header recheck to `tests/unit/test_recorded_value_checks.py` and `tests/integration/test_us3_compose.py`
  - FR-087: the override's header is removed or the file is replaced after the plan but
    before the write (the database step edits it, or the test calls the write step directly
    after changing the file) → `ownership_conflict`, `details.kind` `compose_override`, the
    file byte-identical. The same before the unlink of a recorded override that `up` removes
    because `[compose]` is gone.
- [X] T217 Check the override header again immediately before `up` rewrites or deletes it, in `src/wtenv/compose.py` and `src/wtenv/provision.py`
  - FR-087, cli.md, `wtenv up`, step 5. Call `compose.override_problem` just before
    `write_override` and before the unlink in `_remove_override`, as `teardown` does before
    its unlink. After it, T216 passes.
- [X] T218 Add failing tests for `up` over a SQLite copy left in `removing` to `tests/unit/test_recorded_value_checks.py` and `tests/integration/test_up_recorded_values.py`
  - FR-088: a recorded SQLite copy in state `removing` whose file still exists → `up` exits
    with `unsupported`, `details.reason` `interrupted_removal`, `name` the copy's path; the
    copy, the registry, and every other file are unchanged. After `down`, `up` works.
- [X] T219 Refuse to reuse a SQLite copy in `removing` in `src/wtenv/provision.py`
  - FR-088, cli.md, `wtenv up`, step 7. Raise the error of `_interrupted_removal` (generalised
    to take the name or path) for state `removing` before the copy is adopted. After it,
    T218 passes. The case of `tests/integration/test_recovery.py` that expected `up` to keep a
    SQLite copy left in `removing` now expects `interrupted_removal`, then `down` finishing
    the removal. `data-model.md`, Resource states, still says "keep it and mark `created`" for
    this case: it is outside this group's edit scope and needs the same change.
- [X] T220 Add failing tests for `down` over an env file that git tracks to `tests/integration/test_us4_lifecycle.py`
  - FR-018: `git add -f` of the env file after `up` (once with `created_file` set) → `down`
    exits 13, the `env_section` item is under `failed` with `reason` `tracked_by_git`, the
    entry stays `incomplete`, the file is byte-identical and not deleted. `--dry-run`: the
    item under `failed`, exit 0. An env file that git does not track is removed as before.
- [X] T221 Fail the env-section item of `down` when git tracks the env file, in `src/wtenv/teardown.py`
  - FR-018, cli.md, `wtenv down`. Use `gitutil.is_tracked` in `_env_step`, after the form,
    blocked, and symlink checks and before the record is marked `removing`. After it, T220
    passes.

**Closing the found-after-the-close pairs**

- [X] T222 Check the new group
  - Every ID in T212–T221 is unique, T214–T221 cite an FR that exists in `spec.md`, and the
    full unit and integration suites, ruff, and mypy pass with no integration test skipped.

### Part 2: polish, CI, and release

Runs after group 9S.

- [X] T136 [P] Write a local-only test in `tests/unit/test_local_only.py`
  - Principle I, FR-071: parse every module under `src/wtenv/` with `ast`; no `import` or
    `from … import` names a top-level module in this closed list: `urllib`, `urllib3`,
    `http`, `requests`, `httpx`, `aiohttp`, `ftplib`, `smtplib`, `poplib`, `imaplib`,
    `xmlrpc`, `socketserver`, `ssl`.
    `urllib.parse` is allowed: it only splits strings, and `config.py`, `database.py`, and
    `compose.py` use it (maintainer decision, 2026-10-06).
  - `socket` is imported only by `ports.py` and `doctor.py`, for `bind`.
- [X] T137 [P] Create the sample app in `tests/fixtures/sample_app/app.py`, `tests/fixtures/sample_app/compose.yaml`, `tests/fixtures/sample_app/wtenv.toml`
  - Exactly as quickstart.md, "The sample app fixture".
- [X] T138 Write the provisioning-time test in `tests/integration/test_up_time.py`
  - NFR-002, SC-002 on the sample app (URL rewritten to the `postgres_server` port): (a) a
    first `up` with `[database]` and `post_up` removed; (b) a repeat `up` in a provisioned
    worktree with the full configuration and `post_up` removed; each under 5 s.
- [X] T139 [P] Write the startup benchmark `scripts/bench-startup.sh`
  - NFR-001: against the installed `wtenv` called directly, from the root of a repository
    with only its main worktree and an empty registry (`XDG_STATE_HOME` in a temporary
    directory): `hyperfine --warmup 5 --runs 30 'wtenv --version'` and the same for
    `'wtenv ls --json'`; exit non-zero when a mean is 300 ms or more.
- [X] T140 Run the benchmark on the maintainer's machine and record the result in `docs/benchmarks.md`
  - Machine model, OS version, wtenv version, both means (NFR-001, SC-004). Needs
    `hyperfine`; ask the maintainer if it is missing. If a mean fails, stop and ask: the
    contingency in research.md §8 changes the dependency list.
- [X] T141 [P] Write the CI workflow `.github/workflows/ci.yml`
  - On pushes and pull requests. Matrix `ubuntu-latest` and `macos-latest`, Python 3.11 and
    3.12, with `astral-sh/setup-uv` and `uv sync --locked`. Each job runs the five gates
    (plan.md, Build, CI and release; NFR-004); on macOS the Docker tests skip themselves.
  - The ubuntu jobs have Docker, so the Docker and Postgres integration tests run there
    with nothing skipped.
  - Pin each action to a full commit SHA with its version in a comment, as the uv guide does.
  - Added at the maintainer's request (2026-10-06): a `minimums` job that builds git 2.31.0 and
    installs Compose 2.24.4 from pinned, checksummed downloads and runs both suites against them;
    on every Ubuntu job `lsof` is installed so no port-holder test skips, and no integration
    test may skip (the minimums job allows only the `--orphan` skip of T224).
- [X] T142 Add the NFR-003 coverage check to `.github/workflows/ci.yml`
  - In one ubuntu job: `uv run pytest --cov=wtenv`, then one
    `uv run coverage report --fail-under=80 --include=<modules>` per row of plan.md's
    coverage map: `registry.py` with `locks.py`; `ports.py`; `identity.py`; `config.py`;
    `database.py`; `compose.py`; `orphans.py`; `envfile.py`. Each row measured on its own.
- [X] T143 Run the coverage check locally with Docker available and add tests in `tests/unit/` or `tests/integration/` for any area under 80%
  - Done when all eight core-area rows of T142 report 80% or more.
- [X] T223 Scrub `GIT_INTERNAL_SUPER_PREFIX` too: failing test in `tests/unit/test_gitutil.py`, then `GIT_LOCAL_ENV_VARS` in `src/wtenv/gitutil.py`
  - Found by the T141 minimums job on 2026-10-06 (maintainer approved adding it). `git rev-parse
    --local-env-vars` on git 2.31.0, the minimum, also prints `GIT_INTERNAL_SUPER_PREFIX`; git 2.54
    does not. `tests/integration/test_identity_git.py::test_every_variable_git_calls_repository_local_is_scrubbed`
    fails on 2.31.0 for that reason alone (FR-086).
  - Done when the unit test names it and passes, and the integration test passes on 2.31.0.
- [X] T224 Skip the `--orphan` case of `test_worktree_add_without_a_checkout_runs_no_hook_and_is_silent` below git 2.42, in `tests/integration/test_us5_hook_exec.py`
  - Found by the T141 minimums job on 2026-10-06 (maintainer approved). `git worktree add --orphan`
    exists from git 2.42, so on 2.31.0 git exits 129 before any hook could run. The skip names the
    reason. The minimums job allows this one skip; the other jobs allow none.
- [X] T227 Write a failing test: the help tests under a CI-like environment, in `tests/conftest.py`, `tests/contract/test_cli_shell.py`, `tests/contract/test_command_surface.py`
  - Found by CI run 37663295890 (2026-10-07): on all five jobs that run the unit suite,
    `test_help_works_and_offers_no_shell_completion` and
    `test_the_root_answers_help_and_names_every_command` fail. `GITHUB_ACTIONS=true` (and likewise
    `FORCE_COLOR=1`, `PY_COLORS=1`) makes Typer force Rich to colour, so `--help` carries ANSI codes
    and the box-drawing assertions no longer match. `CI=true` alone does not. The product is not
    wrong: no `--json` or error output carries ANSI codes under forced colour; only `--help` does.
  - A `ci_like_terminal` fixture sets `GITHUB_ACTIONS=true`, `FORCE_COLOR=1`, and `COLUMNS=60` in
    the test process; the two help tests use it, and a new test asserts a command's row in the root
    help is not wrapped. Seen to fail on a laptop before T228.
- [X] T228 Make the `run_wtenv` fixture hermetic, in `tests/conftest.py`
  - The child gets `NO_COLOR=1` and `COLUMNS=200`, and loses `GITHUB_ACTIONS`, `FORCE_COLOR`,
    `PY_COLORS`, and `CI`. `NO_COLOR` alone is not enough: with `GITHUB_ACTIONS` set, bold and dim
    codes remain. Also removed, because each was seen to change `--help`: `TTY_COMPATIBLE` (forces
    colour) and `TERMINAL_WIDTH` (Typer's width, which beats `COLUMNS`). A test's own `env`
    argument still wins. `src/wtenv/` is unchanged.
  - Done when T227 passes, and the unit suite passes both plain and with
    `GITHUB_ACTIONS=true CI=true FORCE_COLOR=1`.
- [X] T229 Write failing tests: a `docker info` that exits 0 with no server version is not running, in `tests/unit/test_compose_checks.py`, `tests/unit/test_doctor_checks.py`, `tests/integration/test_us3_compose.py`
  - Found by CI run 37663295890 (2026-10-07): `test_a_docker_engine_that_a_configuration_needs_and_that_does_not_answer_is_a_problem`
    (US6) fails on the runner, whose Docker CLI is 28.0.4. Docker CLI 26.x to 28.0.x exits 0 with
    empty stdout from `docker info --format {{.ServerVersion}}` when the daemon is dead; 28.1.1 and
    later exit 1. `check_docker` tested only the exit status, so a dead engine counted as up
    (FR-060(e), FR-061; cli.md, `dependency_unavailable` with reason `not_running`).
  - `check_docker`: exit 0 with empty stdout, exit 0 with only whitespace, and exit 1 each raise
    `dependency_unavailable` with reason `not_running`; exit 0 with a version passes.
  - `doctor` (`docker_status`) and `up` (`provision._compose_plan`): the exit-0, empty-stdout case
    is `unavailable` for `doctor` and `dependency_unavailable` for `up`.
  - One integration check: `up` with `[compose]` configured and a dead `DOCKER_HOST` exits 8 with
    `dependency_unavailable`, reason `not_running`.
  - Seen to fail first, before T230.
- [X] T230 Require a server version from `docker info` in `check_docker`, in `src/wtenv/compose.py`
  - The engine counts as running only if `docker info --format {{.ServerVersion}}` exits 0 and
    prints a non-empty version. Nothing else changes.
  - Done when T229 passes, and the US6 test passes against Docker CLI 28.0.4.
- [X] T144 [P] Write the release workflow `.github/workflows/release.yml`
  - On `v*` tags: a `build` job running `uv build` and uploading `dist/`, and a separate
    `publish` job with `environment: pypi` and `permissions: id-token: write` running
    `uv publish` with PyPI trusted publishing; no stored credentials (plan.md; research.md
    §5, §11). The first release uses a "pending" publisher.
  - [X] T225 Write a failing build test in `tests/integration/test_build_contents.py`
    - Maintainer request, 2026-10-07. Runs `uv build` in a temporary directory; the sdist holds
      none of `.claude/`, `.specify/`, `specs/`, `tests/`, `.github/`, `docs/benchmarks.md`,
      `CLAUDE.md`, and holds `src/wtenv/`, `pyproject.toml`, `README.md`, `LICENSE`,
      `CHANGELOG.md`; the wheel holds only `wtenv/` and `*.dist-info/`. Seen to fail first.
  - [X] T226 Limit the sdist in `pyproject.toml` (`[tool.hatch.build.targets.sdist]`)
    - Done when T225 passes and the wheel is unchanged.
- [X] T145 [P] Write `README.md` and add `readme = "README.md"` to `pyproject.toml`
  - Install (`uv tool install wtenv` or `pipx install wtenv`); the nine commands with one
    example each; `wtenv.toml` (config.md); `--json` and exit statuses (cli.md); platforms
    (macOS, Linux, WSL2); limits (research.md §1, §4); working with Claude Code
    (quickstart.md: the `CLAUDE.md` line, no env file in `.worktreeinclude`, `gc` after
    removal). No feature beyond the spec.
  - Added at the maintainer's request (2026-10-06): the problem in three lines; a 60-second demo
    with three parallel worktrees, using real output; install by `uv tool install wtenv` and
    `pipx install wtenv`; the known limits (anonymous volumes stay on disk, the fixed-volume-name
    warning fires only for a volume a service mounts, compose limits, no Windows, Postgres local
    TCP only); an honest comparison with dev containers, hand-rolled scripts, and cloud preview
    environments. Also `LICENSE` (Apache-2.0), `CHANGELOG.md`, and `license` metadata in
    `pyproject.toml`.
- [X] T146 Check `docs/roadmap.md` against the code
  - The command surface matches cli.md (T130); nothing on the roadmap was built; every idea
    noted during implementation is added with its source (FR-001, Principle X).
- [X] T147 Run quickstart.md end to end and fix what it finds
  - `uv tool install --force --reinstall .`, extract the script with the `awk` command at the
    top of quickstart.md, run it with Docker (SC-001–SC-009).
  - Done when the script prints `ACCEPTANCE PASSED`.
  - Found by the run (2026-10-06): the script's `docker rm -f` of its Postgres container left the
    image's anonymous data volume on disk. Both calls now use `docker rm -f -v`. Run twice after
    the fix: `ACCEPTANCE PASSED`, and no container or volume left.
- [X] T148 Run the five gates one last time and confirm `CLAUDE.md` still matches the constitution

### v0.1.1 fixes

Findings of the dogfood run of 0.1.0 on `fastapi/full-stack-fastapi-template`, confirmed by the
maintainer and checked again before any change. Maintainer request, 2026-10-08. No FR, contract,
or JSON model changes in this group; behavior the run showed to be missing (override-file support,
port variables for published ports, `up --dry-run`, and the like) goes to `docs/roadmap.md` and a
separate spec.

- [ ] T231 Write failing tests: `ls` shows the remapped published ports, in `tests/unit/test_render_ls.py`, `tests/integration/test_us3_compose.py`
  - Finding 1 (FR-031: published ports "MUST be shown in the output of `up` and `ls`"). `up`
    prints a `published` line; the text of `ls` shows `PORT=20000` and nothing for the ports the
    compose services publish.
  - Unit: `render_ls_text` for a worktree with a variable port and two published ports shows each
    as `service:target->port` in a `PUBLISHED` column; a port tied to a variable shows in both
    columns; a port that is not tcp shows `/protocol`; when no row publishes a port the header is
    the six columns of today.
  - Integration (Docker): a compose file whose services publish fixed host ports (`backend`,
    `frontend`) is provisioned; the `ls` row names both remapped ports, and `ls --json` carries
    `service`, `target`, `protocol`, and `port` for each (the JSON already does; the test pins it).
  - Seen to fail first, before T232.
- [ ] T232 Show the published ports in the text of `ls`, in `src/wtenv/output.py`
  - `render_ls_text` adds a `PUBLISHED` column between `VARIABLES` and `DATABASE`, only when at
    least one row has a published port. Text is "not a stable interface" (cli.md, Rules), so
    `LsResult` and the JSON are unchanged. The `cli.md` text example is brought in line.
  - Done when T231 passes.
- [ ] T233 Write failing tests: `doctor` changes nothing on a fresh state directory, in `tests/unit/test_locks.py`, `tests/integration/test_us6_diagnostics.py`
  - Finding 2 (FR-060: "without changing anything"). With `XDG_STATE_HOME` pointing at a directory
    that does not exist, `wtenv doctor` creates `<state>/wtenv/registry.lock`.
  - Integration: `XDG_STATE_HOME` is a temporary directory with nothing under it; `doctor` and
    `doctor --json` exit 0 and the listing of the directory is the same before and after. A second
    test: with a registry in place, the state directory is byte-identical after `doctor` too.
  - Unit: a read-only registry lock creates nothing when there is no lock file, and still takes
    the lock when there is one.
  - Seen to fail first, before T234.
- [ ] T234 Make `doctor` take the registry lock without creating it, in `src/wtenv/locks.py`, `src/wtenv/doctor.py`
  - `registry_lock` gets a keyword `create` (default `True`, so every other caller is unchanged).
    With `create=False` and no lock file there is no registry and no writer has ever run, so the
    block runs without a lock; otherwise the lock is taken as before. `diagnose` passes
    `create=False`. Other read-only commands are not touched (see the report of this task group).
  - Done when T233 passes.
- [ ] T235 Correct the README, in `README.md`
  - Finding 3, items (a) to (e) only; nothing is claimed that wtenv does not do. (a) the override
    is named after the compose file (`compose.override.yaml` for `compose.yaml`,
    `compose.override.yml` for `compose.yml`, and so on); (b) the registry lives under
    `XDG_STATE_HOME`; (c) the safe way to merge an override of your own into the compose file, and
    why not to paste `docker compose config` output; (d) Compose reads `.env`, not `.env.local`;
    (e) stop `docker compose watch` and a foreground `up` before `wtenv down`.
  - Each statement was checked against the code or against `docker compose` 5.1.4 first.
- [ ] T236 Record the dogfood ideas, and release 0.1.1, in `docs/roadmap.md`, `src/wtenv/__init__.py`, `CHANGELOG.md`
  - `docs/roadmap.md`: one heading, "From the fastapi-template dogfood run", with the eight ideas
    the maintainer listed (repo-owned override file support is marked BLOCKER). `__version__` and
    the package version (hatch reads it from `__init__.py`) become 0.1.1; `CHANGELOG.md` gets a
    0.1.1 entry listing T231 to T235. No tag.
  - Done when the five gates pass with no skipped integration test, `uv build` succeeds, and the
    sdist test (T225) passes.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup; blocks every story.
- **US1 (Phase 3)**: after Foundational. The MVP.
- **US2 (Phase 4)** and **US3 (Phase 5)**: after US1 (both use the block, the env file, and
  `provision.py`). Independent of each other: the `slug` and `short_id` they both name things
  with are in Foundational (T017). Both edit `config.py`, `provision.py`, and `output.py`, so
  run them one after the other or merge with care.
- **US4 (Phase 6)**: after US1. Groups 6B (database removal) and 6C (compose removal) need
  US2 and US3; without them, skip those groups and the Docker cases.
- **US5 (Phase 7)**: after US4 (`exec` uses `classify`).
- **US6 (Phase 8)**: after US4 (`doctor` uses `classify`); its contract suite covers every
  command delivered so far.
- **Review follow-ups (2026-10-05)**: groups 3F and 6G (FR-086) run after Phase 7, 3F
  first. The review tasks T158–T173 in Phase 9 (part 1) follow them.
- **Review 2 (2026-10-06)**: group 9S (T174–T222) follows part 1. T174 and T175 come
  first; T176–T178 are written next and fail until the pairs they name are done; T211
  closes the group. The pairs run in ID order where they share a file. T212 and T213 were
  added after T211 (FR-087).
- **Polish (Phase 9, part 2: T136–T148)**: after group 9S. T141, T144, and T145 depend
  only on Setup; they were open to being pulled forward for an early MVP release, and now
  wait for group 9S too. T141 must be done before anything merges to `main` (Working
  rules).

### Groups that can run side by side

- Phase 2: 2A, then 2B (needs T008); 2C beside them; 2D after 2B and 2C (T022 imports
  `ResourceState` from T011's `output.py`; T021's lock file names use T017's `short_id`); 2E
  after all.
- Phase 3: 3A, 3B, 3C, and 3D; 3E after all four.
- Phase 4: 4B, then 4C, then 4D, one after the other (all three edit `database.py`); 4A
  beside them; 4E and 4F after them.
- Phase 5: 5B, then 5D, then 5E, one after the other (all three edit `compose.py`); 5A and 5C
  beside them; 5F after them.
- Phase 6: 6A, 6B, and 6C; 6D after them; 6E and 6F after 6D.
- Phase 7: 7A, then 7B; 7C can run beside 7A and 7B.
- Phase 8: 8A, then 8B.

### Inside each group

- Tests first, and seen to fail; then the implementation tasks in order.
- Tasks on the same file run in ID order.

---

## Parallel Example: Phase 2

```text
# Group 2A beside group 2C; 2D waits for 2B and 2C:
Task: "T007 Write failing tests for the error-code table in tests/unit/test_errors.py"
Task: "T013 Write failing tests for git output parsing in tests/unit/test_gitutil.py"
Task: "T014 Write failing tests for points_to … in tests/unit/test_identity.py"
Task: "T015 Write failing integration tests for identity … in tests/integration/test_identity_git.py"
```

## Parallel Example: User Story 1

```text
# Four agents, one group each (3A–3D), each writing its tests first:
Task: "T027 Write failing tests for configuration loading in tests/unit/test_config.py"
Task: "T029 Write failing tests for the free-port test and the block search in tests/unit/test_ports_search.py"
Task: "T033 Write failing tests for writing and reading wtenv's section in tests/unit/test_envfile_write.py"
Task: "T037 Write failing tests for the exclude block in tests/unit/test_exclude.py"
# Then group 3E's three test files together:
Task: "T039 … tests/integration/test_us1_ports_env.py"
Task: "T040 … tests/integration/test_concurrency.py"
Task: "T041 … tests/integration/test_recovery.py"
```

## Parallel Example: User Story 2

```text
# Group 4A beside group 4B; 4C and 4D follow 4B in turn (same database.py):
Task: "T050 … tests/unit/test_config_database.py"
Task: "T052 … tests/unit/test_names.py"
Task: "T053 … tests/unit/test_database_url.py"
```

## Parallel Example: User Story 3

```text
# Groups 5A, 5B, and 5C side by side; 5D and 5E follow 5B in turn (same compose.py):
Task: "T069 … tests/unit/test_config_compose.py"
Task: "T071 … tests/unit/test_compose_model.py"
Task: "T073 … tests/unit/test_ports_published.py"
```

## Parallel Example: User Story 4

```text
Task: "T085 … tests/unit/test_classify.py"
Task: "T087 … tests/unit/test_database_remove.py"
Task: "T090 … tests/unit/test_compose_teardown.py"
# Then:
Task: "T092 … tests/integration/test_us4_lifecycle.py"
Task: "T096 … tests/integration/test_safety_decoys.py"
```

## Parallel Example: User Stories 5 and 6

```text
Task: "T116 … tests/unit/test_hooks_block.py"
Task: "T118 … tests/integration/test_us5_hook_exec.py"
Task: "T124 … tests/unit/test_doctor_checks.py"
Task: "T125 … tests/integration/test_us6_diagnostics.py"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1: Setup. 2. Phase 2: Foundational. 3. Phase 3: US1.
4. **Stop and validate**: the US1 independent test and quickstart.md sections 1–2.
5. Merging the MVP to `main` needs T141 first (Working rules). To publish the MVP
   (research.md §5 suggests an early `0.1.0` to secure the PyPI name), also pull T144 and
   T145 forward; that is the maintainer's choice.

### Incremental delivery

Each phase ends at a checkpoint where the product works and the five gates pass: US1 ports
and env file → US2 databases → US3 compose → US4 `down`, `gc`, `ls` → US5 hook and `exec` →
US6 `doctor` and the full contract → Polish and release.

### Parallel team strategy

After Foundational, independent groups go to separate agents, each in its own worktree
(using wtenv's own `up` once US1 exists). Merge a group only when its tests and the five
gates pass.

---

## Readings taken where the design leaves a choice

The maintainer decided R1 and accepted R2–R4 as written. R5 came from the review of the
destructive paths (2026-10-05). Each is recorded in the contract
named in the last column. R6 and R7 came from the same review; the maintainer
accepted both on 2026-10-05. R8 and R9 came from the second review of the destructive
paths (findings M1 and M2); the maintainer accepted both on 2026-10-06.

| # | Open point | Reading taken | Tasks | Recorded in |
|---|------------|---------------|-------|-------------|
| R1 | Where `exec` gets its variables, given that the registry holds no credentials | `exec` reads every managed variable, ports included, from wtenv's section of the recorded env file (`EnvFileRecord.path`), unquoted per files.md. It does not read `wtenv.toml`. A missing env file, a file with no section, or damaged markers → `env_file_unusable`, exit 125. After `[database]` is removed, the section has no `DATABASE_URL`, so the command gets none | T033, T035, T121, T122 | cli.md, `wtenv exec`; files.md, Env file section |
| R2 | A recorded SQLite copy already deleted by hand, with its side files still there | Report the copy as already absent and leave the side files (FR-039: "never on their own"). A later `up` then copies next to them | T087, T088 | cli.md, `wtenv down` |
| R3 | FR-073 refuses `gc --release` "at which a worktree still exists" | A directory whose `.git` file points to a git directory that no longer exists is not a worktree, so `--release` can release it | T106, T110 | cli.md, `wtenv gc` |
| R4 | files.md: install into an existing "POSIX shell" hook | A shebang naming `sh`, `bash`, `dash`, or `ksh` counts; anything else, or no shebang, is `hook_not_shell` | T116, T117 | files.md, Git hook block |
| R5 | FR-073, for a worktree at the path whose git directory is not the entry's (the repository was moved and repaired, or another worktree took the path) | The converse of R3: a directory whose `.git` names a git directory that exists is a worktree, whatever the entry records, so `--release` refuses it | T149, T150 | cli.md, `wtenv gc` |
| R6 | FR-041, for anonymous volumes that `compose down --volumes` removes without a project label (review LOW-4) | **Accepted; amended 2026-10-05 after a probe (Docker 29.5.3, Compose 5.1.4): only named volumes carry `com.docker.compose.project`, anonymous and external volumes do not.** `down` and `gc` stop passing `--volumes` to `docker compose down`. wtenv lists volumes labelled `com.docker.compose.project=<recorded project name>`, removes each by name, and reports each as its own item in `removed` and in `--dry-run`. A volume without the label is never removed. Before `docker compose down` (and for `--dry-run`), wtenv inspects the project's containers' mounts; every mounted volume lacking the project label, anonymous or external, is reported in `kept_volumes` (a `KeptVolume` with `project` the recorded name and reason `unlabelled`) on `DownResult` and `GcResult`; `kept` is unchanged. Known limit: anonymous volumes of removed projects stay on disk (docs/roadmap.md) | T172, T173, T162, T163 | cli.md, `wtenv down` |
| R7 | FR-073 and FR-074, for a `--release` entry whose worktree appears after step 1 (review LOW-7) | **Accepted.** `gc --release` re-checks that the worktree is still gone immediately before each delete. If it has reappeared, it stops that entry with `worktree_exists`, exit 18 | T170, T171 | cli.md, `wtenv gc` |
| R8 | FR-039, for a compose volume declared with a fixed `name:`, which gets the label of whichever project created it first and is shared with the main checkout and other worktrees (review 2, M1) | **Accepted.** `down` and `gc` remove only labelled volumes whose name starts with `<project>_`. Any other volume with the project label is never removed and is reported in `kept_volumes` with reason `fixed_name` (`KeptVolume.reason` is `"unlabelled"` or `"fixed_name"`). `up` adds the warning `compose_fixed_volume_name` when the resolved compose model has a non-external volume whose name is not `<model name>_<key>`. Known limit: such a volume stays on disk even when this worktree's project created it | T174, T175, T185–T188 | cli.md, `wtenv up` and `wtenv down`; json_models.py; data-model.md, Compose record |
| R9 | FR-045 and FR-072, for a worktree on a drive that is not mounted or under a renamed directory, after git has pruned it (`gc.worktreePruneExpire`), which passes every FR-045 check (review 2, M2) | **Accepted.** An entry whose recorded path has a missing parent directory is `unverifiable`, new reason `parent_missing`, never `orphaned`; it is released only by `gc --release`. The check is step 4.3 of the classification, so the reasons of steps 1 to 4.2 are unchanged and `moved` still blocks `--release` | T174, T175, T189, T190 | cli.md, Worktree status; data-model.md, Status and the orphan checks; json_models.py; spec.md, FR-072 |

---

## Notes

- [P] tasks touch different files and need nothing unfinished.
- The story label maps each task to spec.md for traceability; FR, NFR, and SC numbers in the
  task text map it to requirements.
- FR-055 and US5 scenario 4 are not delivered (spec.md, Clarifications); they have no tasks.
- Commit after each completed group; stop at any checkpoint to validate a story on its own.
