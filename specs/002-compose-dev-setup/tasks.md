---
description: "Task list for spec 002: repository-owned overrides, port variables, and compose-hosted databases"
---

# Tasks: Compose dev setups — repository-owned overrides, port variables, and compose-hosted databases

**Input**: Design documents from `/specs/002-compose-dev-setup/`

**Prerequisites**: plan.md, spec.md (clarified 2026-10-10), research.md (R1–R6), data-model.md,
contracts/ (cli.md, files.md, config.md, json_models.py), quickstart.md, constitution v1.0.2

**Tests**: included and written first (constitution Principle VII; plan.md, Test strategy). Each
group starts with tests that fail for the expected reason, then the code, then a group check.

**Organization**: the maintainer set this order (2026-10-10), which differs from the spec's priority
order: shared foundations, then User Story 2 (P2, port variables), then User Story 1 (P1,
repository-owned override and `exec`), then User Story 3 (P3, compose-hosted database), then
compatibility, docs and release. Story 2 comes first because it changes no write or delete path
and also works for repositories v0.1.x already supports (spec, User Story 2). Story labels keep
the spec's numbers.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run at the same time as the [P] tasks next to it (different files, no dependency on
  an unfinished task).
- **[Story]**: `[US1]` repository-owned override and `exec`, `[US2]` port variables, `[US3]`
  compose-hosted database. Setup, Foundational, Compatibility and Polish tasks have no label.
- **⚠RELAX(001:FR-030)** and **⚠RELAX(001:FR-087 outcome)** mark the tasks that relax a spec 001
  requirement (spec, "Relationship to spec 001"; plan.md, "Relaxations of spec 001,
  summarised"). The safety review starts from the index at the end of this file.
- **Safety group** marks a group that changes a write, delete or environment-injection path. Each
  of its guards has a mutation-check task.
- Every task names its files, the requirement(s) it covers, and a **Done when** line that a
  command can check. Indented lines under a task belong to it.
- Short names: `cli.md`, `files.md`, `config.md`, `json_models.py` are in
  `specs/002-compose-dev-setup/contracts/`; `data-model.md`, `research.md`, `quickstart.md` are in
  `specs/002-compose-dev-setup/`. `001:` cites spec 001. `S1`–`S7` are the sections of plan.md,
  Safety.

## Path Conventions

Single project with a `src/` layout, as in spec 001: `src/wtenv/`, `tests/unit/` (no git
worktrees, no Docker), `tests/contract/`, `tests/integration/` (marker `integration`),
`tests/fixtures/`, `docs/`, `.github/workflows/`. One new source module: `src/wtenv/isolation.py`
(standard library only; plan.md, Structure Decision).

## Working rules

- **No implementation outside a task.** Build only what a task says. An idea outside the spec goes
  to `docs/roadmap.md` (Principle X). If a task is ambiguous, stop and ask.
- **`specs/001-worktree-runtime-isolation/` is not edited** by any task. The 002 contracts state
  only what changes.
- **Test-first.** Run a group's new tests and see them fail for the expected reason before writing
  the code. "Expected reason" means an assertion, or a `NotImplementedError` from a stub. Never an
  `ImportError`, `ModuleNotFoundError` or `AttributeError` on a missing name. When a test needs a
  function or module that does not exist yet, the test task adds it as a stub: the signature, the
  docstring, and `raise NotImplementedError`. A failing-test task is done when
  `uv run pytest <files> -q 2>&1 | grep -E 'ImportError|ModuleNotFoundError|AttributeError: module'`
  prints nothing and pytest exits 1.
- **Existing tests.** No existing test changes its expected outcome, except the five named in
  T025 and T032. Five, not the three of plan.md's table: the maintainer decided on 2026-10-10 to
  rewrite all five. An existing test may be *extended* (more cases, more assertions) where a task
  says so. If any other existing test starts failing, stop and ask. Do not edit it.
- **Mutation check** (plan.md, Test strategy). For the guard the task names: remove or break it in
  the working tree, run the named test(s) and see them fail, then restore the file with
  `git checkout -- <file>` (or `git stash pop` of the clean state). The mutation is never
  committed. Under the task, record the test ID and the first assertion line of its failure.
  **Done when** the test exits 1 with the guard removed, and after the restore
  `git diff --quiet -- src/` exits 0 and the test exits 0.
- **G5, the five gates.** Every group check runs this block from the repository root, with Docker
  available. A group check is done when the block prints `G5 PASSED`:

  ```sh
  set -o pipefail
  uv run pytest -m "not integration" \
  && uv run pytest -m integration -rs | tee "${TMPDIR:-/tmp}/wtenv-g5-integration.log" \
  && ! grep '^SKIPPED' "${TMPDIR:-/tmp}/wtenv-g5-integration.log" | grep -v -- '--orphan' \
  && uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy src \
  && git diff --quiet main -- specs/001-worktree-runtime-isolation \
  && echo "G5 PASSED"
  ```

  The `--orphan` skip exists only on git older than 2.42 (001 T224). No other skip is allowed.
- **Commits.** One commit per completed group (`###`), after its group check. Conventional Commits.
  No `Co-Authored-By`, "Generated with", or session-link trailers. Never commit `.env*` files. Never
  force-push.
- **Integration tests use only what they create** (plan.md, Test strategy). Compose projects,
  volumes and networks are named `wtenv-test-<random>` or are wtenv projects of worktrees the test
  created. Decoy volumes carry a random suffix and are removed in a `finally`. No test lists or
  removes a resource it did not create, except to assert that it is untouched. Docker tests skip
  with "Docker is not available", as today.
- **Code**: full type hints, docstrings on public functions, plain functions and frozen
  dataclasses or pydantic models, no metaprogramming (Principle IX). `exec` must not import
  `wtenv.compose` (NFR-001).

---

## Phase 1: Setup

**Purpose**: a green baseline, and v0.1.1's outputs captured by v0.1.1 itself, for the
compatibility tests (FR-008, FR-028, FR-029).

- [ ] T001 Confirm the baseline: run G5 on the tip of `002-compose-dev-setup` (the commit that adds this file), changing no file in `src/` or `tests/`
  - Nothing is edited. If a gate fails, stop and ask: the failure predates spec 002.
  - **Done when** G5 prints `G5 PASSED` and `git status --porcelain` prints nothing.
- [ ] T002 Capture v0.1.1's outputs with v0.1.1's own code, in `tests/fixtures/v0_1_1/make_golden.py`, `tests/fixtures/v0_1_1/configs/`, and `tests/fixtures/v0_1_1/golden/`
  - FR-008, FR-028, FR-029. The golden files are produced by v0.1.1 (tag `v0.1.1`), never written by
    hand and never by the new code.
  - `configs/`: one `.toml` per configuration the v0.1.1 test suite uses, copied verbatim:
    `tests/fixtures/sample_app/wtenv.toml`; `TOML`, `REMAPPED_TOML`, `QUIET_TOML`, `NO_COMPOSE_TOML`
    and `MOVED_TOML` of `tests/integration/test_us3_compose.py`; `SQLITE_TOML` and `COMPOSE_TOML` of
    `tests/integration/test_us1_ports_env.py` and `test_us4_lifecycle.py`; a Postgres configuration
    with `{env:PGPASSWORD}`. For every compose configuration, `configs/<name>.compose.json` holds
    the `docker compose config --format json` output of the compose file it uses, so the script
    needs no Docker.
  - `make_golden.py` takes `--out DIR`. For each configuration it calls only the public functions
    both versions share: `config.load_config`, `ports.assign_variable_ports`,
    `ports.assign_published_ports`, `compose.parse_model`, `compose.project_name`,
    `compose.override_text`, `database.postgres_database_name`, `database.sqlite_copy_path`,
    `envfile.write_section`. The inputs are fixed: block start 20000, worktree name `feature-x`,
    git directory `/golden/repo/.git/worktrees/feature-x`. It writes `<name>.json` (the config
    dump, ports, project name, database name or path, env-section text) and
    `<name>.override.yaml`. It also writes `registry.json` through v0.1.1's `registry.save` of a
    `Registry` holding one compose, one Postgres, one SQLite and one plain entry built from
    those results.
  - Produced with `git worktree add --detach "$SCRATCH/v0.1.1" v0.1.1` and
    `uv run --project "$SCRATCH/v0.1.1" python -I tests/fixtures/v0_1_1/make_golden.py --out tests/fixtures/v0_1_1/golden`,
    then `git worktree remove "$SCRATCH/v0.1.1"`.
  - **Done when** running the same two commands with `--out "$SCRATCH/regen"` and then
    `diff -r "$SCRATCH/regen" tests/fixtures/v0_1_1/golden` prints nothing, and
    `uv run ruff check tests/fixtures/v0_1_1` exits 0.

**Checkpoint**: baseline green; v0.1.1 goldens committed with the group (`test(fixtures): …`).

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the contract test against 002's `json_models.py`, error codes and exit statuses 20–23,
the optional registry field, and the `[database] type = "compose"` configuration model.

**⚠️ CRITICAL**: no user-story phase starts until Phase 2 is complete.

### 2A. Contracts, error codes and exit statuses (`errors.py`, `output.py`, contract tests)

- [ ] T003 [P] Point the contract tests at spec 002's contract and add failing member checks, in `tests/contract/conftest.py`, `tests/contract/test_models_match_contract.py`, and `tests/contract/test_exit_statuses.py`
  - FR-031, FR-032. `CONTRACTS` in `conftest.py` becomes
    `specs/002-compose-dev-setup/contracts` (002's `json_models.py` is the full schema-1 contract
    with 002 additions marked `002:`). `SCHEMA_VERSION` is still 1.
  - `test_exit_statuses.py` reads the code table from 001 `cli.md` **and** the "new rows" table of
    002 `cli.md`, and requires both to agree with `errors.EXIT_STATUS`: 1–19, 125–127 unchanged,
    plus 20 `isolation_unavailable`, 21 `override_ambiguous`, 22 `port_variable_conflict`, 23
    `database_storage_shared`.
  - Explicit member checks: the four `ErrorCode` members and values; `WarningCode` gains
    `compose_isolation_exec_only`, `compose_database_data_kept`, `compose_override_adopted`;
    `PortView.port_variable`; `WorktreeView.compose_isolation` (`"automatic" | "exec_only" | None`)
    and `compose_repository_override`; `KeptItem` (`Item` + `reason: "repository_owned"`);
    `ComposeDatabaseView` (service, target, protocol, port, port_variable, url_variable);
    `UpResult.compose_database`; `DownResult.kept_items`, `GcResult.kept_items`.
  - **Done when** `uv run pytest tests/contract -q` exits 1 and every failure names a 002 member
    (the failing-test rule of Working rules holds).
- [ ] T004 [P] Add failing tests for codes 20–23 to `tests/unit/test_errors.py`
  - FR-031: 23 codes, each with exactly one status, no status used twice, the four new ones as in
    T003; `wtenv.errors` still imports only the standard library.
  - **Done when** `uv run pytest tests/unit/test_errors.py -q` exits 1 with assertion failures only.
- [ ] T005 Add the four codes and their exit statuses to `src/wtenv/errors.py`
  - FR-031; plan.md, "Error codes, exit statuses, warnings and reasons". No existing code or status
    changes.
  - **Done when** `uv run pytest tests/unit/test_errors.py -q` exits 0.
- [ ] T006 Port the 002 members of `json_models.py` into `src/wtenv/output.py`
  - FR-031, FR-032. Models and enums only; no command emits them yet. New fields default to `None`
    or an empty list, so every v0.1.1 document still validates.
  - **Done when** `uv run pytest tests/contract -q` exits 0.
- [ ] T007 Group check 2A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(contracts): add spec 002 error codes, warnings and models`.

### 2B. Registry: one optional field (`registry.py`)

- [ ] T008 Add failing tests for `ComposeRecord.repository_override` to `tests/unit/test_registry_models.py` and `tests/unit/test_registry_store.py`
  - FR-029; data-model.md, Registry; plan.md S7.
  - New `test_a_v0_1_1_registry_is_saved_byte_for_byte`: load `tests/fixtures/v0_1_1/golden/registry.json`
    (T002) and save it to a temporary state directory. The bytes are identical.
  - An entry with `repository_override=None` serialises with no `repository_override` key. An entry
    with it set round-trips. `REGISTRY_VERSION` is still 1.
  - Kept, and must still pass: `test_a_password_cannot_be_stored_in_a_database_record` (FR-020),
    `test_a_volumes_list_added_to_the_compose_record_makes_the_registry_unreadable` (001:FR-070).
  - **Done when** the two files fail under the failing-test rule.
- [ ] T009 Add `repository_override: str | None = Field(default=None, exclude_if=lambda value: value is None)` to `ComposeRecord` in `src/wtenv/registry.py`
  - FR-029. The consistency rule ("set if and only if `override` is
    `.wtenv/compose.isolation.yaml`") is **not** checked by the model, so that a hand-edited
    registry is reported rather than unreadable (data-model.md).
  - **Done when** `uv run pytest tests/unit/test_registry_models.py tests/unit/test_registry_store.py -q` exits 0.
- [ ] T010 Mutation check: `exclude_if` on the new field, in `src/wtenv/registry.py` (restored afterwards)
  - Guard: serialise only when set (S7). Remove `exclude_if`; `test_a_v0_1_1_registry_is_saved_byte_for_byte` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T011 Group check 2B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(registry): record a repository-owned override when one is used`.

### 2C. Configuration: `[database] type = "compose"` (`config.py`)

- [ ] T012 [P] Add failing configuration tests to `tests/unit/test_config_database.py`
  - FR-017, FR-018, FR-020, FR-028; config.md, "Settings: changed rows" and "The URL pattern for
    `type = "compose"`". Each case quotes its rule:
    - `type = "compose"` with `service` and `url` and a `[compose]` table is accepted.
    - `type = "compose"` without `[compose]`: `config_invalid`, `setting` `database.type`.
    - `template` with `compose`: `config_invalid`, `setting` `database.template`, message
      "not a setting of type compose". `service` with `postgres` or `sqlite`: `config_invalid`,
      `setting` `database.service`. `service` missing with `compose`: `config_invalid`.
    - URL: scheme `postgresql://` or `postgres://`, with or without a driver suffix; host exactly
      `localhost`, `127.0.0.1` or `[::1]`; `{port}` as the port "and nothing else there"; a
      database name present; a `host`, `hostaddr` or `service` query parameter refused;
      `{name}` and `{path}` refused; `{env:NAME}` and `{compose_env:NAME}` accepted.
    - "The message of any pattern error never repeats the pattern": with a password in the
      pattern, no error text contains it (extends `test_a_rejected_pattern_does_not_reveal_a_password`
      with compose cases).
    - Every `tests/fixtures/v0_1_1/configs/*.toml` loads, and its `model_dump(mode="json")` equals
      the `config` value in its golden JSON (FR-028, part 1).
  - **Done when** the file fails under the failing-test rule.
- [ ] T013 Accept `type = "compose"`, `service`, and the compose URL rules in `src/wtenv/config.py`
  - FR-017, FR-018. One database mode per worktree. No other setting is added (FR-063 extended
    only by these keys).
  - **Done when** `uv run pytest tests/unit/test_config_database.py tests/unit/test_config.py tests/unit/test_config_compose.py -q` exits 0.
- [ ] T014 Group check 2C: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(config): add the compose database type`.

**Checkpoint**: contracts, codes 20–23, registry field and configuration model in place; no command
behaves differently yet.

---

## Phase 3: User Story 2 — Remapped published ports are available as named variables (Priority: P2)

**Goal**: every published port has a variable `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>`
in wtenv's env-file section and in `exec`, shown by `up` and `ls`.

**Independent Test**: spec, User Story 2, "Independent Test", with the existing `valkey` stack
(`tests/integration/helpers.py`, `COMPOSE_IMAGE`). The "request reaches the backend" check sends
`PING` over a socket to the port in the variable and expects `+PONG`; with worktree 2's container
stopped, worktree 2's port refuses and worktree 1's still answers (SC-002).

### 3A. The name rule and its collisions (`ports.py`)

- [ ] T015 [P] [US2] Add failing tests for the rule and collisions to `tests/unit/test_port_variables.py`
  - FR-011, FR-012, SC-008; research.md R5; files.md, "Env file section".
  - New `test_the_variable_name_depends_only_on_service_port_and_protocol`: the 15 worked examples
    of R5 verbatim (`adminer` → `WTENV_PORT_ADMINER_8080_TCP` … `a-b`/`a--b` distinct); the rule
    is "upper case with every character other than `A–Z` and `0–9` replaced by `_` (one `_` per
    character; nothing is trimmed or collapsed)". The name is the same for any block, host port,
    host IP and service order.
  - Collisions → `port_variable_conflict` (exit 22), `details.variable` the name, `details.sources`
    two strings of the forms `service <name>, container port <n>/<protocol>`, `wtenv.toml ports`,
    or `DATABASE_URL`: `my-api` and `my_api`; `API` and `api`; `web` 6379/tcp published twice
    (E1.6b); a name equal to a `ports` entry; a name equal to `DATABASE_URL` when `[database]` is
    set.
  - SC-008: for every compose model under `tests/` (the `.compose.json` files of T002 and every
    `STACK` constant of `tests/integration/test_us3_compose.py`), either all names are distinct or
    the check raises. Never one name for two ports.
  - **Done when** the file fails under the failing-test rule.
- [ ] T016 [US2] Implement `port_variable_name()` and `check_port_variables()` in `src/wtenv/ports.py`
  - FR-011, FR-012, FR-015: pure functions; no port is assigned for a variable.
  - **Done when** `uv run pytest tests/unit/test_port_variables.py -q` exits 0.
- [ ] T017 [US2] Mutation check: the collision check, in `src/wtenv/ports.py` (restored afterwards)
  - Guard: "two different published ports MUST never share a variable name" (FR-012). Make
    `check_port_variables` return without checking; the collision cases of T015 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T018 [US2] Group check 3A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(ports): derive a variable name for every published port`.

### 3B. The variables in the env file and in `exec` (`provision.py`)

- [ ] T019 [US2] Add failing integration tests to `tests/integration/test_us2_port_variables.py` (new; `pytestmark = pytest.mark.integration`; Docker)
  - FR-010, FR-012, FR-013, FR-015, FR-016, FR-028, SC-002. One test per scenario of User Story 2:
    - 1: the section has one `WTENV_PORT_…` line per published port with the assigned host port,
      and `wtenv exec -- env` sets the same value.
    - 2: a port tied to a `ports` variable (001:FR-031) also has its rule-derived variable with the
      same number.
    - 3: two worktrees have the same set of names and different values; the socket `PING` reaches
      each worktree's own container (SC-002).
    - 4: a repeat `up` leaves the env file byte-identical (extend
      `test_a_repeat_up_leaves_the_project_name_and_the_override_byte_identical` in
      `tests/integration/test_us3_compose.py` to the env file, as plan.md says).
    - 5: a published port removed from the compose file loses its variable in the section and in
      `exec`.
    - 6: new `test_a_name_collision_changes_nothing`: exit 22 before step 8; worktree tree, env file,
      registry bytes, override and exclude block unchanged.
  - FR-013 order: `ports` variables in `ports` order, then port variables sorted by (service,
    container port, protocol), then `DATABASE_URL`.
  - FR-015: the block size needed is unchanged (`test_check_block_size_counts_the_published_ports`
    kept).
  - FR-016: new `test_without_compose_the_section_is_as_in_v0_1_1`: no `WTENV_PORT_` line.
  - FR-028: a section written by v0.1.1 (the env-section text of the T002 golden) is reported
    `updated` by the first `up`, and the only new lines are `WTENV_PORT_…`.
  - Edge case: a developer's line outside the section that sets a `WTENV_PORT_…` name is left
    alone with the 001:FR-080 duplicate warning.
  - **Done when** the new file fails under the failing-test rule.
- [ ] T020 [US2] Write the port variables in `_variables` and check them in `_compose_plan`, in `src/wtenv/provision.py`
  - FR-010, FR-012, FR-013. The collision check runs in step 5, before step 8 (cli.md, `wtenv up`).
    `exec` needs no change: it reads the section (001:FR-056).
  - **Done when** `uv run pytest tests/integration/test_us2_port_variables.py tests/integration/test_us3_compose.py -q` exits 0.
- [ ] T021 [US2] Group check 3B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): write a variable for every published port`.

### 3C. Showing the variables in `up` and `ls` (`listing.py`, `output.py`)

- [ ] T022 [P] [US2] Add failing tests for the views to `tests/unit/test_render_up.py`, `tests/unit/test_render_ls.py`, and `tests/integration/test_us2_port_variables.py`
  - FR-014; User Story 2 scenario 7; cli.md, `wtenv up` text and `wtenv ls`.
  - `PortView.port_variable` is set for every published port and `None` for a `ports` variable.
    `up` text: `backend:8000 -> 20002 (WTENV_PORT_BACKEND_8000_TCP)`; a tied port:
    `db:5432 -> 20011 (DB_PORT, WTENV_PORT_DB_5432_TCP)`. `ls` text cell:
    `service:container-port->host-port (NAME)`. JSON of `up` and `ls` carries `port_variable`.
  - Existing render tests build views without `port_variable` and must pass unchanged.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T023 [US2] Set `port_variable` in `listing.port_views` and render it in `src/wtenv/listing.py` and `src/wtenv/output.py`
  - **Done when** `uv run pytest tests/unit/test_render_up.py tests/unit/test_render_ls.py tests/integration/test_us2_port_variables.py -q` exits 0.
- [ ] T024 [US2] Group check 3C: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(output): show port variables in up and ls`.

**Checkpoint**: User Story 2 works on its own for every repository v0.1.1 supports.

---

## Phase 4: User Story 1 — A repository that owns its override file can use wtenv (Priority: P1)

**Goal**: `up` succeeds beside a repository-owned override, writes `.wtenv/compose.isolation.yaml`,
and `exec` and post-up commands apply it through `COMPOSE_FILE`. The repository's file is never
written, moved, deleted or excluded.

**Independent Test**: spec, User Story 1, "Independent Test": a compose file that publishes no
ports and a committed override that publishes them and sets a command. Two worktrees, each stack
started through `wtenv exec`, run at the same time on their own block ports, and the override is
byte-identical throughout.

### 4A. Override inventory and arrangement, read only (`compose.py`)

- [ ] T025 [US1] ⚠RELAX(001:FR-030) Rewrite four existing tests that assert the v0.1 refusal, in `tests/unit/test_compose_checks.py`
  - Maintainer decision 2026-10-10: plan.md's table names three tests; these four plus T032's
    are the five to rewrite. Each now calls `compose.override_inventory` and keeps its point:
    - `test_any_of_the_four_override_names_that_is_not_recorded_is_an_ownership_conflict` →
      exec-only arrangement with that file as the repository override; file unchanged (FR-001).
    - `test_a_second_override_name_beside_the_recorded_one_is_a_conflict` → wtenv's recorded file
      is marked for removal, the repository's file is honoured (FR-007).
    - `test_only_the_directory_of_the_compose_file_is_searched` → a root override beside
      `deploy/compose.yaml` is not counted (automatic); one in `deploy/` is the repository override
      (exec-only) (FR-001).
    - `test_an_override_recorded_beside_the_old_compose_file_does_not_cover_the_new_directory` →
      `deploy/compose.override.yaml` is the repository override (exec-only); wtenv's root file is
      not counted beside `deploy/compose.yaml` (FR-001, FR-007).
  - Each test is renamed to say its new outcome, and its old name goes in its docstring, so the
    trace survives. No other test in the file changes.
  - **Done when** the four tests fail under the failing-test rule (stub `override_inventory`).
- [ ] T026 [P] [US1] Add failing tests for the inventory to `tests/unit/test_override_inventory.py` (new)
  - FR-001, FR-006 (classification only), FR-007; data-model.md, "Override inventory (step 5)".
    Every row of the classification table: `wtenv_own`; `adopt` (recorded path, no header, git
    tracks it, using a real `git init` in `tmp_path`); `ownership_conflict` (recorded path, no
    header, untracked; details `kind` `compose_override`, `name` the path); `repository_owned`
    (any other of the four names).
  - Counts: 0 → `automatic`; 1 → `exec_only`, every `wtenv_own` file marked for removal; ≥ 2 →
    `override_ambiguous` (exit 21), `details.files` absolute and sorted, every file unchanged.
  - A symbolic link at the recorded path is refused first (001:FR-086, unchanged). A
    repository-owned override that is a link is accepted and only read.
  - **Done when** the file fails under the failing-test rule.
- [ ] T027 [US1] Implement `override_inventory()` and the arrangement result in `src/wtenv/compose.py`
  - Read only: it writes, renames and deletes nothing. `check_override_files` stays until T033
    switches `up` to the inventory.
  - **Done when** `uv run pytest tests/unit/test_override_inventory.py tests/unit/test_compose_checks.py -q` exits 0.
- [ ] T028 [US1] Group check 4A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(compose): classify override files beside the compose file`.

### 4B. Resolving the compose file with the repository override (`compose.py`)

- [ ] T029 [P] [US1] Add failing tests for the combined model to `tests/unit/test_compose_model.py` and `tests/unit/test_compose_checks.py`
  - FR-003, FR-015; research.md R1, R2; cli.md, `wtenv up` step 5.
  - `resolution_command(file, repository_override)` is `docker compose -f <file> -f <override>
    --profile "*" config --format json`; without an override it is exactly v0.1.1's command.
  - A published range in the repository override: `unsupported`, `compose_port_range`, naming
    the service (001:FR-033).
  - A port only the override publishes is in the model; a fixed host port, or one from a
    variable, is replaced by the assigned port while `host_ip`, `protocol`, `mode`, `name` and
    `app_protocol` are kept (fed to the unchanged `override_text`).
  - Block size counts the combined model (`config_invalid` with the smallest block that fits).
  - Port variables (T016) are computed over the combined model; E1.6b (the override publishes the
    same container port on another host port) is `port_variable_conflict`.
  - An absolute path of the compose file, the repository override or the isolation file
    containing `:`: `unsupported`, reason `compose_path_separator`, `details.file` that path.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T030 [US1] Accept the repository override in `resolution_command` and `resolve_model`, and add the `:` check, in `src/wtenv/compose.py`
  - **Done when** `uv run pytest tests/unit/test_compose_model.py tests/unit/test_compose_checks.py -q` exits 0.
- [ ] T031 [US1] Group check 4B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(compose): resolve the compose file together with the repository override`.

### 4C. Safety group — writing the isolation file (`provision.py`, `isolation.py`; plan.md S1)

- [ ] T032 [US1] ⚠RELAX(001:FR-030) Add failing tests for the exec-only arrangement in `up`, in `tests/integration/test_us1_repository_override.py` (new), `tests/integration/test_us3_compose.py`, `tests/integration/test_recovery.py`, `tests/unit/test_compose_write.py`, and `tests/unit/test_isolation.py` (new)
  - Rewrite the fifth test, `tests/integration/test_us3_compose.py::test_a_developers_override_is_an_ownership_conflict_and_is_not_modified`,
    in place → exit 0, `compose_isolation` `exec_only`, the committed override byte-identical,
    `.wtenv/compose.isolation.yaml` written (FR-001, FR-002). Rename it to
    `test_a_developers_override_is_used_through_exec_and_is_not_modified`, with the old name in
    its docstring.
  - New, in `test_us1_repository_override.py`, one per scenario of User Story 1: 1 (exit 0,
    override byte-identical, new `test_git_status_is_clean_after_up_in_the_exec_only_arrangement`);
    2 and 8 (the override publishes `backend` on fixed host port 8000 and sets `command`; through
    `wtenv exec -- docker compose config` the port is a block port and the command is the
    override's, FR-003); 4 (a repeat `up` gives identical ports, project and isolation-file
    bytes); 6 (no repository override: v0.1.1's override name and bytes, compared with
    `tests/unit/golden/compose.override.yaml`, FR-008).
  - R1 pin, new `test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose`:
    two-service `valkey` stack, repository override with fixed ports; started with
    `wtenv exec -- docker compose up -d`; `docker ps` for the project shows only block ports.
  - S1 guards, one test each: record before create (extend
    `test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers` in
    `test_recovery.py` with an exec-only fixture, stopping after every step); new
    `test_an_unrecorded_file_at_the_isolation_path_is_an_ownership_conflict_and_is_kept`
    (`kind` `compose_override`); header recheck before rewrite (parametrise
    `test_up_does_not_rewrite_an_override_that_lost_the_header_after_the_plan` over both
    arrangements); new `test_a_symlinked_wtenv_directory_is_refused_in_the_exec_only_arrangement`
    (`.wtenv/` and the file; `env_file_unusable`, `symlink`); exclude (the git-status test above,
    plus `/.wtenv/compose.isolation.yaml` in the exclude block and never the repository override's
    path); verification (parametrise `test_a_replaced_override_that_fails_verification_is_removed_too`
    and `test_an_override_that_fails_verification_is_removed_and_the_error_is_raised` in
    `test_compose_write.py` over the isolation file).
  - `COMPOSE_PROJECT_NAME` / `COMPOSE_FILE` in the environment or in `.env` is still `unsupported`
    in the exec-only arrangement (spec, Edge cases).
  - `tests/unit/test_isolation.py`: `isolation.compose_environment(root, record)` returns exactly
    `COMPOSE_FILE=<root>/<file>:<root>/<repository_override>:<root>/.wtenv/compose.isolation.yaml`
    and `COMPOSE_PATH_SEPARATOR=:`, and never `COMPOSE_PROJECT_NAME` (data-model.md, Isolation
    environment).
  - **Done when** these files fail under the failing-test rule.
- [ ] T033 [US1] ⚠RELAX(001:FR-030) Write and verify the isolation file in the exec-only arrangement, in `src/wtenv/isolation.py` (new), `src/wtenv/provision.py`, and `src/wtenv/compose.py`
  - FR-001, FR-002, FR-003, FR-009; cli.md, `wtenv up` steps 5, 8, 11; data-model.md,
    Transitions rows "none → `automatic`" and "none → `exec_only`".
  - `_compose_plan` uses `override_inventory` (remove `check_override_files` and its last caller).
    In the exec-only arrangement: the isolation-path checks of cli.md step 5; `_registry_step`
    records `override = .wtenv/compose.isolation.yaml` (`creating`) and `repository_override`
    before step 11, and adds `/.wtenv/compose.isolation.yaml` to `exclude_patterns`;
    `_compose_step` rechecks the header immediately before the write and calls
    `write_and_verify_override` with the environment from `isolation.compose_environment`.
  - `isolation.py`: standard library only; `compose_environment` only in this task (the `exec`
    checks come in T052).
  - The repository override's path is never passed to a write, unlink, chmod or exclude call
    (FR-002).
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_us3_compose.py tests/integration/test_recovery.py tests/unit/test_compose_write.py tests/unit/test_isolation.py -q` exits 0.
- [ ] T034 [US1] Mutation check: record before create (001:FR-067), in `src/wtenv/provision.py` (restored afterwards)
  - Move the `_registry_step` save of the isolation record after the write; the exec-only case of
    `test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T035 [US1] Mutation check: an unrecorded file at the isolation path is refused, in `src/wtenv/provision.py` (restored afterwards)
  - Remove the check; `test_an_unrecorded_file_at_the_isolation_path_is_an_ownership_conflict_and_is_kept` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T036 [US1] Mutation check: header recheck before rewriting the isolation file (001:FR-087), in `src/wtenv/provision.py` (restored afterwards)
  - Remove the recheck immediately before the write; the exec-only case of
    `test_up_does_not_rewrite_an_override_that_lost_the_header_after_the_plan` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T037 [US1] Mutation check: no symbolic link at `.wtenv/` or the isolation file (001:FR-086), in `src/wtenv/provision.py` (restored afterwards)
  - Remove `_refuse_symlink(root, ".wtenv/compose.isolation.yaml")`;
    `test_a_symlinked_wtenv_directory_is_refused_in_the_exec_only_arrangement` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T038 [US1] Mutation check: the isolation file is excluded from `git status` (001:FR-018), in `src/wtenv/provision.py` (restored afterwards)
  - Drop the exclude pattern; `test_git_status_is_clean_after_up_in_the_exec_only_arrangement` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T039 [US1] Mutation check: verification removes only the file just written (001:FR-033), in `src/wtenv/compose.py` (restored afterwards)
  - Skip the removal on verification failure; the isolation-file case of
    `test_a_replaced_override_that_fails_verification_is_removed_too` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T040 [US1] Group check 4C: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): write a recorded isolation file beside a repository-owned override`.

### 4D. Safety group — removing wtenv's own override on a change of arrangement (`provision.py`; plan.md S2)

- [ ] T041 [US1] Add failing tests for arrangement transitions to `tests/integration/test_us1_repository_override.py` and `tests/integration/test_us3_compose.py`
  - FR-002, FR-007, FR-008, FR-009; data-model.md, Transitions; spec, Edge cases.
  - New `test_a_repository_override_appearing_removes_only_wtenvs_own_override`: `automatic` →
    `exec_only` when a repository override appears under another name. wtenv's file is removed
    (header rechecked), the isolation file is written, the repository file is byte-identical.
  - `exec_only` → `automatic` when the repository override is deleted: the isolation file is
    removed, v0.1.1's override is written (golden bytes), `repository_override` is cleared.
  - `exec_only` → `exec_only` under another override name: `repository_override` updated, the
    isolation file rewritten only if its text changed.
  - `[compose]` removed in `exec_only`: the isolation file is removed under the header check,
    `repository_override` cleared, the repository file untouched.
  - Header lost after the plan, before the unlink: parametrise
    `test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan` over the transition
    callers. Kept: `test_a_new_compose_file_moves_the_override_and_keeps_the_project_name`.
  - An `up` interrupted in each transition leaves `creating`/`removing`, and the next `up`
    finishes it.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T042 [US1] Carry out the transitions through the existing path-change branch and `_remove_override`, in `src/wtenv/provision.py`
  - The old record is `removing` before the unlink; the header is rechecked immediately before
    it; the new file is `creating` before it is written. No new unlink call site.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_us3_compose.py -q` exits 0.
- [ ] T043 [US1] Mutation check: header recheck immediately before the unlink in `_remove_override` (001:FR-087), in `src/wtenv/provision.py` (restored afterwards)
  - Remove it; the transition cases of `test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T044 [US1] Mutation check: `removing` is recorded before the unlink, in `src/wtenv/provision.py` (restored afterwards)
  - Record `removing` after the unlink; the interrupted-transition case of T041 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T045 [US1] Group check 4D: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): switch compose arrangement when a repository override appears or goes`.

### 4E. Safety group — adopting a tracked file at the recorded path in `up` (`compose.py`, `provision.py`; plan.md S3)

- [ ] T046 [US1] ⚠RELAX(001:FR-087 outcome) Add failing tests for adoption by `up` to `tests/unit/test_override_inventory.py` and `tests/integration/test_us1_repository_override.py`
  - FR-006; data-model.md Transitions row "`automatic` → `exec_only` by adoption"; files.md,
    `.git/info/exclude` block.
  - New `test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict`
    (exit 11; file, registry and tree unchanged).
  - Tracked and header-less: exit 0; `override` → `.wtenv/compose.isolation.yaml`,
    `repository_override` → the old path; the old path's pattern leaves the entry's
    `exclude_patterns` (the line stays in the block); warning `compose_override_adopted` with
    `details.file`; the file byte-identical, mode and mtime unchanged.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T047 [US1] ⚠RELAX(001:FR-087 outcome) Adopt a tracked header-less file in `src/wtenv/compose.py` and `src/wtenv/provision.py`
  - Both conditions are required: header missing **and** `gitutil.is_tracked` true. Adoption
    changes only the registry.
  - **Done when** `uv run pytest tests/unit/test_override_inventory.py tests/integration/test_us1_repository_override.py -q` exits 0.
- [ ] T048 [US1] Mutation check: `is_tracked` in adoption by `up`, in `src/wtenv/compose.py` (restored afterwards)
  - Drop the tracked condition; `test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T049 [US1] Mutation check: adoption never writes or deletes the file, in `src/wtenv/provision.py` (restored afterwards)
  - Route the adopted path through `_remove_override`; the byte-identical assertion of T046 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T050 [US1] Group check 4E: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): adopt a tracked override at the recorded path as the repository's`.

### 4F. Safety group — environment injection by `exec` and post-up commands (`isolation.py`, `execcmd.py`, `provision.py`; plan.md S5)

- [ ] T051 [US1] ⚠RELAX(001:FR-030) Add failing tests for FR-033 to `tests/unit/test_isolation.py`, `tests/integration/test_us1_exec_isolation.py` (new), and `tests/unit/test_lazy_imports.py`
  - FR-004, FR-033; data-model.md, "`exec` checks (FR-033), in order"; cli.md, `wtenv exec`.
  - New `test_exec_does_not_run_the_command_when_isolation_cannot_apply`, parametrised by the seven
    reasons in table order: `compose_env_override` (`COMPOSE_PROJECT_NAME`, then `COMPOSE_FILE`,
    non-empty; `details.variable`); `recorded_value_invalid` (`override` not the isolation path,
    or `repository_override`/`file` failing `recorded_path_problem`); `symlink` (`path` the link);
    `isolation_file_missing`; `isolation_file_not_wtenvs`; `repository_override_missing`;
    `override_appeared` (`path`). Each: exit 125, `error.code` `isolation_unavailable`,
    `details.path` absolute, and a sentinel command (`touch <file>`) did **not** run. When two
    conditions hold, the first in table order is reported.
  - New `test_exec_never_sets_compose_project_name`; new
    `test_exec_in_an_automatic_worktree_sets_exactly_the_section` (the v0.1.1 environment plus only
    the section's variables).
  - The environment reaches a child (`sh -c`, a `make` target) and works from a subdirectory
    (research R2, E2.3, E2.4).
  - New `test_post_up_commands_get_the_same_compose_environment_as_exec` (001:FR-036): a post-up
    command prints `COMPOSE_FILE` and `COMPOSE_PATH_SEPARATOR`, equal to what `exec` sets.
  - User Story 1 scenario 10.
  - `tests/unit/test_lazy_imports.py`: new `test_exec_does_not_load_the_compose_module` (NFR-001).
  - **Done when** these files fail under the failing-test rule.
- [ ] T052 [US1] ⚠RELAX(001:FR-030) Apply the FR-033 checks and the isolation environment in `src/wtenv/isolation.py`, `src/wtenv/execcmd.py`, and `src/wtenv/provision.py`
  - The checks run only when the entry records `repository_override`, after the `provisioned`
    check, and before `os.execvpe`. On failure: `isolation_unavailable`, exit 125, nothing run.
  - `_run_post_up` takes its environment from the same `isolation.compose_environment`.
    `COMPOSE_PROJECT_NAME` is never set.
  - **Done when** `uv run pytest tests/unit/test_isolation.py tests/integration/test_us1_exec_isolation.py tests/unit/test_lazy_imports.py -q` exits 0.
- [ ] T053 [US1] Mutation check: FR-033 check 1, `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE` in the calling environment, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `compose_env_override` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T054 [US1] Mutation check: FR-033 check 2, recorded values, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `recorded_value_invalid` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T055 [US1] Mutation check: FR-033 check 3, symbolic links, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `symlink` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T056 [US1] Mutation check: FR-033 check 4, the isolation file exists, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `isolation_file_missing` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T057 [US1] Mutation check: FR-033 check 5, the isolation file's header, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `isolation_file_not_wtenvs` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T058 [US1] Mutation check: FR-033 check 6, the repository override exists, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `repository_override_missing` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T059 [US1] Mutation check: FR-033 check 7, no other override name appeared, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `override_appeared` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T060 [US1] Mutation check: `COMPOSE_PROJECT_NAME` is not injected, in `src/wtenv/isolation.py` (restored afterwards)
  - Add it to `compose_environment`; `test_exec_never_sets_compose_project_name` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T061 [US1] Group check 4F: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(exec): apply the isolation file in worktrees with a repository-owned override`.

### 4G. Reporting the arrangement (`output.py`, `listing.py`, `provision.py`)

- [ ] T062 [P] [US1] ⚠RELAX(001:FR-030) Add failing tests for FR-005 to `tests/unit/test_render_up.py`, `tests/unit/test_render_ls.py`, and `tests/integration/test_us1_repository_override.py`
  - FR-004, FR-005, FR-032; User Story 1 scenario 9; cli.md, `wtenv up` text and `wtenv ls`.
  - JSON: `worktree.compose_isolation` (`automatic`, `exec_only`, or null without compose) and
    `compose_repository_override`, in `up` and `ls`. Warning `compose_isolation_exec_only` with
    `details.repository_override` and `details.isolation_file` on **every** `up` in exec-only.
  - Text: the `isolation` line of cli.md, which says Compose is isolated only through
    `wtenv exec -- docker compose ...` and that a plain `docker compose` runs WITHOUT isolation.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T063 [US1] Report the arrangement in `src/wtenv/output.py`, `src/wtenv/listing.py`, and `src/wtenv/provision.py`
  - **Done when** `uv run pytest tests/unit/test_render_up.py tests/unit/test_render_ls.py tests/integration/test_us1_repository_override.py -q` exits 0.
- [ ] T064 [US1] Group check 4G: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(output): report the compose arrangement in up and ls`.

### 4H. Safety group — deleting the isolation file in `down` and `gc`, and keeping adopted files (`compose.py`, `teardown.py`; plan.md S3, S4)

- [ ] T065 [US1] ⚠RELAX(001:FR-087 outcome) Add failing tests for teardown to `tests/integration/test_us1_repository_override.py`, `tests/integration/test_hand_edited_registry.py`, and `tests/unit/test_teardown_paths.py`
  - FR-002, FR-006, FR-009, SC-007; User Story 1 scenario 5; cli.md, `wtenv down` and `wtenv gc`.
  - New `test_down_removes_the_isolation_file_and_never_the_repository_override`: `down --dry-run`
    and `down` list and remove `.wtenv/compose.isolation.yaml`. The repository override is in none
    of `removed`, `would_remove`, `already_absent`, `failed`, `kept_items`, and is byte-identical.
    The same for `gc` and `gc --release`.
  - Extend `test_a_recorded_path_of_a_form_wtenv_never_records_is_left_alone` with
    `.wtenv/notes.yaml`, `.wtenv/sub/compose.isolation.yaml` and `../.wtenv/compose.isolation.yaml`:
    each is under `failed`, left alone, record kept.
  - New `test_a_tracked_headerless_file_at_the_recorded_path_is_kept_by_down_and_reported_in_kept_items`:
    `kept_items` with `kind` `compose_override`, `name` the path, `reason` `repository_owned`; not
    under `failed`; record dropped only when the project step did not fail; the same with
    `--dry-run`, `gc`, `gc --release`. Kept: `test_a_recorded_override_that_is_not_wtenvs_is_never_deleted_by_down_or_gc`
    (untracked case: still `failed`).
  - New decoy test `test_a_hand_edited_repository_override_record_is_never_deleted`:
    `repository_override` hand-edited to a developer file in the worktree, to `../outside.yml`,
    and to an absolute path; `down --dry-run`, `down`, `gc`, `gc --release` never list or touch the
    decoy.
  - **Done when** these cases fail under the failing-test rule.
- [ ] T066 [US1] ⚠RELAX(001:FR-087 outcome) Accept the exact isolation path in `override_problem` and report adopted files in `kept_items`, in `src/wtenv/compose.py` and `src/wtenv/teardown.py`
  - `override_problem` accepts the four names **or exactly** `.wtenv/compose.isolation.yaml`;
    nothing else under `.wtenv/`. `_override_step`: a header-less file that `gitutil.is_tracked`
    reports is a `KeptItem`, not a `FailedItem`, and is never unlinked. `down` and `gc` look only
    at `ComposeRecord.override`, never at `repository_override`.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_hand_edited_registry.py tests/unit/test_teardown_paths.py -q` exits 0.
- [ ] T067 [US1] Mutation check: exact-path acceptance in `override_problem`, in `src/wtenv/compose.py` (restored afterwards)
  - Widen it to any `.wtenv/*.yaml`; the `.wtenv/notes.yaml` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T068 [US1] Mutation check: `is_tracked` in the teardown outcome, in `src/wtenv/teardown.py` (restored afterwards)
  - Drop it; the untracked case of `test_a_recorded_override_that_is_not_wtenvs_is_never_deleted_by_down_or_gc` must fail (it would be kept, not failed).
  - **Done when** as in Working rules, "Mutation check".
- [ ] T069 [US1] Mutation check: header check before deleting the isolation file, in `src/wtenv/teardown.py` (restored afterwards)
  - Remove it in `_override_step`; a case of T065 with a header-less file at the isolation path must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T070 [US1] Mutation check: only `ComposeRecord.override` is ever deleted, in `src/wtenv/teardown.py` (restored afterwards)
  - Make teardown also unlink `repository_override`; `test_a_hand_edited_repository_override_record_is_never_deleted` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T071 [US1] Group check 4H: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(down): remove the isolation file and keep repository-owned overrides`.

### 4I. User Story 1 end to end

- [ ] T072 [US1] Add end-to-end tests for User Story 1 to `tests/integration/test_us1_repository_override.py`
  - User Story 1 scenarios 2, 3 (three worktrees started at the same time through `wtenv exec`:
    no shared host port, container, network or project-scoped volume), 4; SC-005 (10 repeats of
    `up`, with and without a repository override: identical ports, project, env file and
    isolation-file bytes); SC-007; new
    `test_the_repository_override_is_byte_identical_through_up_down_gc_and_dry_runs`.
  - Edge cases: the override edited after provisioning (the next `up` recomputes; unchanged
    ports keep their numbers); the override is a symbolic link (read only; works).
  - These test behaviour built in 4A–4H. Run them; a failure goes to T073.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py -q` has been
    run and its result (all passed, or the failing test IDs) is recorded under this task.
- [ ] T073 [US1] Fix what T072 finds, in the module the failure points at
  - Only within FR-001 to FR-009 and FR-033. A failure that needs a new behaviour: stop and ask.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py -q` exits 0
    (or T072 passed first, and this task says so).
- [ ] T074 [US1] Group check 4I: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `test(us1): run three worktrees with a repository-owned override end to end`.

**Checkpoint**: User Story 1 works; the fastapi template provisions in exec-only mode.

---

## Phase 5: User Story 3 — A database that runs as a Compose service gets a per-worktree connection setting (Priority: P3)

**Goal**: with `[database] type = "compose"`, `up` writes `DATABASE_URL` for the service's
remapped port, refuses shared data storage, and teardown is the unchanged compose teardown.

**Independent Test**: spec, User Story 3, "Independent Test", with `postgres:17` (already pulled
in CI) and a named project volume.

### 5A. The URL (`database.py`)

- [ ] T075 [P] [US3] Add failing tests for compose URLs to `tests/unit/test_database_url.py`
  - FR-018, FR-020; config.md, "The URL pattern for `type = "compose"`".
  - `{port}` → the host port. `{env:NAME}` → the process environment, verbatim (v0.1.x).
    `{compose_env:NAME}` → the service's resolved `environment[NAME]`,
    `urllib.parse.quote(value, safe="")` (a password with `@`, `/`, `:` and `%`). A name the
    service does not have: `config_invalid`, `setting` `database.url`, `details.variable`. An
    unset `{env:NAME}`: `config_invalid` as today.
  - Step 7 check with a stand-in port fails for the same two cases before anything changes.
  - New `test_a_compose_url_must_name_a_local_host` (001:FR-025).
  - No error message contains the resolved password.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T076 [US3] Resolve `{port}` and `{compose_env:NAME}` in `resolve_url`, in `src/wtenv/database.py`
  - **Done when** `uv run pytest tests/unit/test_database_url.py -q` exits 0.
- [ ] T077 [US3] Group check 5A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(database): build the URL of a compose-hosted database`.

### 5B. The database service's port and its data storage (`compose.py`)

- [ ] T078 [P] [US3] Add failing tests for the port and the storage classifier to `tests/unit/test_compose_database.py` (new)
  - FR-021, FR-026, FR-027; data-model.md, "Compose-hosted database" and "Data storage
    classification"; research R4.
  - `database_port`: no such service, zero published ports, two or more → `config_invalid`,
    `setting` `database.service`, naming the service and what was found.
  - Storage, one case per table row, data mounts only (`volume` or `bind`, not `read_only`): none
    → warning `no_volume`; `volume` without `source` → warning `anonymous_volume`; `external: true`
    → `database_storage_shared` (exit 23) `external_volume`; top-level `name` ≠
    `<model name>_<key>` → `fixed_name_volume`; `driver_opts.device` = D → judged as a bind of D;
    `<model name>_<key>`, no device → accepted, no warning; bind with `realpath(source)` inside
    `realpath(root)` → warning `worktree_directory`; outside → `database_storage_shared`
    `host_directory` with `details.name` the real path.
  - "The first refusal found, in mount order, is reported. Warnings are collected for every
    accepted mount." A read-only bind outside is ignored.
  - New `test_a_bind_through_a_symlink_to_outside_the_worktree_is_refused`.
  - Other services' fixed-name volumes still only warn `compose_fixed_volume_name` (R8 unchanged).
  - **Done when** the file fails under the failing-test rule.
- [ ] T079 [US3] Implement `database_port()` and `database_storage()` in `src/wtenv/compose.py`
  - **Done when** `uv run pytest tests/unit/test_compose_database.py -q` exits 0.
- [ ] T080 [US3] Mutation check: FR-027 row "external volume", in `src/wtenv/compose.py` (restored afterwards)
  - Classify `external: true` as accepted; its case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T081 [US3] Mutation check: FR-027 row "fixed-name volume" (R8 name rule), in `src/wtenv/compose.py` (restored afterwards)
  - Classify a fixed `name` as project-scoped; its case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T082 [US3] Mutation check: FR-027 row "`driver_opts.device`", in `src/wtenv/compose.py` (restored afterwards)
  - Ignore `device`; the case of a project-named volume whose device is outside the worktree must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T083 [US3] Mutation check: FR-027 row "bind outside the worktree" (real paths), in `src/wtenv/compose.py` (restored afterwards)
  - Compare unresolved paths instead of `realpath`; `test_a_bind_through_a_symlink_to_outside_the_worktree_is_refused` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T084 [US3] Group check 5B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(compose): find the database port and refuse shared data storage`.

### 5C. `up` with a compose-hosted database (`provision.py`, `output.py`)

- [ ] T085 [US3] Add failing integration tests to `tests/integration/test_us3_compose_database.py` (new; Docker; `postgres:17`)
  - FR-012, FR-019, FR-020, FR-021, FR-022, FR-024, FR-026, FR-027; User Story 3 scenarios 1–4,
    6, 7; config.md, "Changes after provisioning: new rows".
  - 1: the section and `exec` carry `DATABASE_URL` with `localhost:<the db port variable's
    value>` and the port variable; `UpResult.compose_database` (service, target, protocol,
    port, port_variable, url_variable); no `DatabaseRecord`; psycopg is not imported by `up`, and
    no template is copied.
  - 2 and SC-003: three worktrees, each `db` started through `wtenv exec`; a row written through
    `wtenv exec` in each is found only in its own database.
  - 3: a tracked `.env` with `localhost:5432`: `exec` carries the worktree's URL; `.env` is
    byte-identical; `git status` clean.
  - 4: a repeat `up` keeps port, URL and the written row (FR-024).
  - 6: missing service / no port / two ports → exit 3, nothing changed (new
    `test_a_database_service_with_two_ports_changes_nothing`). 7: fixed-name, external, outside
    bind → exit 23, `details.service`, `storage`, `name`; nothing changed.
  - FR-026: anonymous volume, no volume, worktree directory → exit 0 with
    `compose_database_data_kept`.
  - FR-022: extend `test_up_starts_no_container_and_creates_no_network_or_volume` (in
    `tests/integration/test_us3_compose.py`) with a compose-database configuration.
  - FR-020: new `test_no_up_output_contains_the_compose_database_password` (text, JSON, warnings,
    errors, registry bytes).
  - FR-012: a published port whose name equals `DATABASE_URL` cannot happen by the rule; a
    `ports` entry named like the db port's variable → exit 22.
  - Config changes: `postgres` → `compose` (URL rebuilt, old database stays recorded until
    `down`); `compose` → `sqlite` (created as in v0.1.x); `service` changed (URL and port follow).
  - **Done when** the new file fails under the failing-test rule.
- [ ] T086 [US3] Plan and write the compose-hosted database in `src/wtenv/provision.py` and `src/wtenv/output.py`
  - Step 5: `database_port` and `database_storage`. Step 7: stand-in URL check. Step 10:
    nothing. Step 12: `DATABASE_URL` last in the section (FR-013). `_has_nothing_to_change`
    covers the URL. Text line `database   compose service db on localhost:<port>  (DATABASE_URL)`.
  - **Done when** `uv run pytest tests/integration/test_us3_compose_database.py tests/integration/test_us3_compose.py -q` exits 0.
- [ ] T087 [US3] Group check 5C: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): provision a compose-hosted database`.

### 5D. Safety group — teardown of the compose-hosted database (no new code by design; plan.md S6)

- [ ] T088 [US3] Add teardown tests for the compose-hosted database to `tests/integration/test_us3_compose_database.py`
  - FR-023, SC-004, SC-007; User Story 3 scenario 5.
  - New `test_down_removes_the_database_container_and_its_project_volume_only`: planted decoys,
    each created by the test with a random suffix and removed in a `finally`: a fixed-name volume
    `wtenv-test-fixed-<random>`, an external volume `wtenv-test-ext-<random>`, and another project
    `wtenv-test-other-<random>` with its own volume. `down --dry-run` lists the `db` container
    and `<project>_<volume>`; `down` removes exactly those; every decoy survives with its
    contents. The same through `gc` after `git worktree remove`.
  - Kept: `test_a_labelled_volume_with_a_fixed_name_is_kept_and_the_projects_own_is_removed`.
  - FR-023 says teardown needs no new code. If a case fails, stop and ask. Do not add teardown code.
  - **Done when** `uv run pytest tests/integration/test_us3_compose_database.py -q` exits 0 and
    `git diff --quiet HEAD -- src/` exits 0 (no source change in this group).
- [ ] T089 [US3] Mutation check: the `<project>_` prefix rule (reading R8) in `compose.remove_project`, in `src/wtenv/compose.py` (restored afterwards)
  - Remove the prefix filter; the fixed-name decoy case of T088 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T090 [US3] Group check 5D: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `test(us3): prove teardown of a compose-hosted database touches only its project`.

**Checkpoint**: all three stories work.

---

## Phase 6: Compatibility and the agent contract (cross-cutting)

- [ ] T091 Add failing compatibility and agent-contract tests to `tests/unit/test_v0_1_1_compatibility.py` (new) and `tests/contract/test_json_documents.py`
  - FR-008, FR-028, FR-030, FR-032, SC-006, SC-009.
  - New `test_every_v0_1_1_config_fixture_is_accepted_and_provisions_identically`: for each
    T002 configuration, the new code's results from the same public functions and fixed inputs
    equal the golden ports, project name, database name or path, and override bytes; the env
    section equals the golden apart from the added `WTENV_PORT_…` lines.
  - SC-009: every new error code (20–23 and the new `reason` values) and every new warning, when
    produced by its command with `--json` and standard input closed, is exactly one JSON
    document on stdout that validates against `json_models.py`.
  - Expected to pass if Phases 2–5 are right. Run them; a failure goes to T092.
  - **Done when** `uv run pytest tests/unit/test_v0_1_1_compatibility.py tests/contract/test_json_documents.py -q`
    has been run and its result (all passed, or the failing test IDs) is recorded under this task.
- [ ] T092 Fix what T091 finds, in the module the failure points at
  - A difference from v0.1.1 that FR-028 does not allow is a bug. Do not change the golden.
  - **Done when** `uv run pytest tests/unit/test_v0_1_1_compatibility.py tests/contract -q` exits 0.
- [ ] T093 Run spec 001's acceptance script (SC-006)
  - `uv tool install --force --reinstall .`, then the script of
    `specs/001-worktree-runtime-isolation/quickstart.md`, extracted with the `awk` command at its
    top, with Docker. Read-only use of the 001 directory.
  - **Done when** the script prints `ACCEPTANCE PASSED`.
- [ ] T094 Group check Phase 6: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `test(compat): pin v0.1.1 compatibility and the agent contract for spec 002`.

---

## Phase 7: Documentation, CI and release preparation

### 7A. Documentation (`README.md`, `docs/roadmap.md`)

For these tasks the failing check is the task's `grep` command, run before the edit and seen to
fail.

- [ ] T095 [P] ⚠RELAX(001:FR-030) Document spec 002 in `README.md`
  - FR-004, FR-011, FR-025; cli.md, "User documentation (README) obligations".
  - **Override and `exec`**: in a repository that tracks its own Compose override, Compose
    commands are isolated **only when run through `wtenv exec`**; how to start the stack
    (`wtenv exec -- docker compose up -d`); post-up commands get the same isolation.
  - **The plain `docker compose` caveat**, in plain words: a plain `docker compose` in such a
    worktree runs **without isolation**, on the repository's fixed ports, under a project name
    Compose derives from the directory, which wtenv does not record and `wtenv down` never
    removes; it can collide with the main checkout's stack when both directories have the same
    name. A command's own `-p` or `-f` wins over the isolation file.
  - **Port-variable naming rule**: `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>`, the
    character rule, examples from files.md, and that a name collision fails with
    `port_variable_conflict` (exit 22).
  - **Compose-hosted database**: the config.md example (`type = "compose"`, `service`, `{port}`,
    `{compose_env:NAME}` percent-encoded); that the port and URL variables are **host-side
    values** and a container that receives one through interpolation gets the host-side value;
    `database_storage_shared` (exit 23) and how to fix it; data kept on `down` for anonymous
    volumes and directories inside the worktree.
  - **Known limits** (replace the `ownership_conflict` line of "Compose"): the exec-only caveat;
    Compose 2.24.4 reads `.env` from the current directory, so run Compose from the compose
    file's directory there; one container port published twice is refused; every writable mount
    of the database service counts as data (use `:ro`); fixed `image:` tags are shared between
    worktrees; `isolation_unavailable` reasons and fixes; exit statuses 20–23 in the status table.
  - **Done when** each of these prints at least one line:
    `grep -n 'WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>' README.md`,
    `grep -n 'without isolation' README.md`, `grep -n 'type = "compose"' README.md`,
    `grep -n 'host-side' README.md`, `grep -n 'isolation_unavailable' README.md`,
    `grep -n 'port_variable_conflict' README.md`, `grep -n '2.24.4' README.md`; and
    `grep -c 'already exists and wtenv did not' README.md` prints `0` (the v0.1 refusal line is
    gone).
- [ ] T096 [P] Update `docs/roadmap.md`
  - Principle X; plan.md, "Roadmap entries". New heading "From planning feature 002
    (2026-10-10)" with the four entries: a `--project-directory` equivalent so Compose 2.24.4 reads
    `.env` from any subdirectory; showing a compose-hosted database in `ls`; inspecting the image's
    `VOLUME`/`PGDATA` to narrow FR-026/FR-027; naming two host addresses of one container port
    apart. In "From the fastapi-template dogfood run", mark the three rows delivered in 0.2.0
    (spec 002), noting the variable names are `WTENV_PORT_…`, not the `WTENV_BACKEND_8000` example.
    Add every idea noted during implementation, with its source.
  - **Done when** `grep -n 'From planning feature 002' docs/roadmap.md` and
    `grep -c 'spec 002' docs/roadmap.md` (≥ 3) both succeed.
- [ ] T097 Group check 7A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `docs: document overrides, port variables and compose-hosted databases`.

### 7B. CI and release preparation (`.github/workflows/ci.yml`, `CHANGELOG.md`, `src/wtenv/__init__.py`)

- [ ] T098 Make the `minimums` job (Compose 2.24.4) run the new integration tests with nothing skipped, in `.github/workflows/ci.yml`
  - Plan.md, Test strategy and Coverage. In `minimums`: `pytest -m integration -rA` so passes are
    listed; a step that fails unless the log has `PASSED` for
    `test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose` and has
    lines for `test_us1_repository_override.py`, `test_us1_exec_isolation.py`,
    `test_us2_port_variables.py` and `test_us3_compose_database.py`; the existing no-skip step
    unchanged (only `--orphan` allowed). Pull every image the new tests use (expected:
    `postgres:17` and `valkey/valkey:latest`, already pulled; add any other in all three jobs).
  - `coverage` job: add `uv run coverage report --fail-under=80 --include='*/wtenv/isolation.py'`.
  - Actions stay pinned to full SHAs.
  - **Done when** the branch is pushed (normal push, never force) and
    `gh run list --branch 002-compose-dev-setup --workflow CI --limit 1 --json conclusion -q '.[0].conclusion'`
    prints `success`, and
    `gh run view <id> --log --job <minimums job id> | grep -c 'PASSED .*test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose'`
    prints at least 1.
- [ ] T099 [P] Add the 0.2.0 entry to `CHANGELOG.md`
  - Keep a Changelog format, `## [0.2.0] - <date of this task>` under `[Unreleased]`. **Added**:
    repository-owned override support (exec-only arrangement, `.wtenv/compose.isolation.yaml`,
    `wtenv exec`/post-up isolation), port variables, `[database] type = "compose"`, error codes
    20–23, the three warnings, `kept_items`. **Changed**, stated as a behaviour change: a compose
    setup that publishes one container port twice (for example a repository override that adds a
    second host port for the same container port) now fails with `port_variable_conflict`
    (exit 22), where v0.1.1 accepted it; and existing compose users see new `WTENV_PORT_…` lines in
    wtenv's env-file section, reported `updated` on the first `up`. **Known limits**: a plain
    `docker compose` in an exec-only worktree is not isolated.
  - **Done when** `grep -n '## \[0.2.0\]' CHANGELOG.md` and
    `awk '/## \[0.2.0\]/,/## \[0.1.1\]/' CHANGELOG.md | grep -n 'port_variable_conflict'` both print a line.
- [ ] T100 [P] Bump the version to 0.2.0 in `src/wtenv/__init__.py`, without tagging
  - The single source of the version (hatch reads it). No tag, no push of a tag, no release.
  - **Done when** `uv run wtenv --version` prints `0.2.0`, `git tag --list 'v0.2*'` prints
    nothing, `uv build` succeeds, and `uv run pytest tests/integration/test_build_contents.py -q`
    exits 0.
- [ ] T101 Group check 7B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `chore(release): prepare 0.2.0`.

### 7C. Acceptance

- [ ] T102 Run quickstart.md on `fastapi/full-stack-fastapi-template` in three worktrees and check SC-001 to SC-009
  - Every step of quickstart.md, once with the maintainer's newest Compose and once with Compose
    2.24.4. For 2.24.4, `DOCKER_CONFIG="$ACC/docker-2.24.4"` holds only
    `cli-plugins/docker-compose` (the v2.24.4 release binary for this platform, its checksum
    checked against the release's `checksums.txt`), and `DOCKER_HOST` is set to the endpoint
    `docker context inspect` reports, so `~/.docker` is not changed;
    `docker compose version --short` prints `2.24.4`.
  - Uses only containers, volumes, networks and names the run creates. Step 0 baseline
    (`docker ps -a`, `docker volume ls`, `docker network ls`) is diffed at step 7. The only
    differences allowed are the template's images and the `backend:latest` tag.
  - SC-001 (step 3), SC-002 (step 4), SC-003 (step 5), SC-004 (step 7), SC-005 (step 6, plus
    the 10-run loop in a scratch repository without an override), SC-006 (T093's result),
    SC-007 (step 7 diffs, plus T065, T072, T088 passing), SC-008 (T015 passing), SC-009 (every
    `wtenv` call of the run uses `--json` or reads variables only, no prompt, standard input
    `</dev/null`).
  - A failure is fixed in a new task pair added to this file (failing test first), then the run
    is repeated from step 0.
  - **Done when**, for both Compose versions: step 3 prints `SC-001 ports: 15 distinct, all in block`;
    step 4 prints three `ok` lines and `wt2 refused` for the stopped backend; step 5 prints
    `wt1 sees ['wt1']`, `wt2 sees ['wt2']`, `wt3 sees ['wt3']`; step 6 prints `3` and
    `command not run`; step 7 prints `0` and three `override intact, isolation file gone` lines;
    and the three baseline `diff` commands print only the allowed image lines (no container,
    volume or network line). The outputs are recorded under this task.
- [ ] T103 Final check
  - G5 once more; the coverage rows of `.github/workflows/ci.yml` locally
    (`uv run pytest --cov=wtenv` then each `coverage report --fail-under=80 --include=…`,
    including `isolation.py`); `CLAUDE.md` still matches the constitution; every task in this file
    is checked.
  - **Done when** G5 prints `G5 PASSED`, every coverage row exits 0,
    `git diff --quiet main -- specs/001-worktree-runtime-isolation` exits 0, and
    `grep -c '^- \[ \] T' specs/002-compose-dev-setup/tasks.md` prints `0`.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1** (T001–T002): none. T002 must finish before T008, T012, T091.
- **Phase 2** (T003–T014): after Phase 1. 2A, 2B and 2C are independent of each other; all three
  block every story.
- **Phase 3, US2** (T015–T024): after Phase 2. 3A → 3B → 3C.
- **Phase 4, US1** (T025–T074): after Phase 3 (the port-variable collision check runs over the
  combined model in 4B). 4A → 4B → 4C → {4D, 4E, 4F, 4G} → 4H → 4I. 4D, 4E, 4F and 4G each need
  4C; 4H needs 4E (adopted files) and 4C.
- **Phase 5, US3** (T075–T090): after Phase 4 (the database service is found in the combined
  model; teardown runs in both arrangements). 5A and 5B are independent; 5C needs both; 5D needs 5C.
- **Phase 6** (T091–T094): after Phase 5.
- **Phase 7** (T095–T103): 7A after Phase 6; 7B after 7A; 7C last.

### Inside each group

Failing tests → code → mutation checks (safety groups) → group check → commit. Mutation checks of
one group can run in any order, one at a time, on a clean working tree.

## Parallel examples

```text
Phase 2:  T003 (contract tests) | T004 (errors tests) | T012 (config tests)
Phase 3:  T015 (rule tests) while 2C finishes; T022 after T020
Phase 4:  T026 (inventory tests) | T029 (combined-model tests)
          after T040: 4D, 4E, 4F, 4G on separate branches of work, merged before 4H
Phase 5:  T075 (URL tests) | T078 (storage tests)
Phase 7:  T095 (README) | T096 (roadmap); T099 (CHANGELOG) | T100 (version)
```

## Implementation strategy

- **First increment**: Phases 1–3. User Story 2 alone ships value for every repository v0.1.1
  supports, and changes no write or delete path.
- **MVP for the dogfood target**: through Phase 4. The fastapi template provisions, and its stacks
  run isolated through `wtenv exec`.
- **Complete feature**: Phase 5 (compose-hosted database), then compatibility, docs, CI, and the
  acceptance run. No release or tag is part of this task list.

## Safety review index

### Tasks that relax a spec 001 requirement

| Relaxation | Tasks | Containment (plan.md) |
|------------|-------|-----------------------|
| ⚠RELAX(001:FR-030): plain `docker compose` is not isolated in exec-only worktrees | T025, T032, T033, T051, T052, T062, T095 | S5: only when a repository-owned override is detected; reported in text, JSON and the `compose_isolation_exec_only` warning; documented; automatic arrangement unchanged (T032 scenario 6, T051 `test_exec_in_an_automatic_worktree_sets_exactly_the_section`) |
| ⚠RELAX(001:FR-087 outcome): a git-tracked header-less file at the recorded path is adopted/kept instead of `ownership_conflict`/`failed` | T046, T047, T065, T066 | S3: header check unchanged; both conditions required; never written or deleted; untracked case unchanged (T046, T068) |

### Mutation checks, one per guard

| Guard | Plan section | Task |
|-------|--------------|------|
| `exclude_if` on `repository_override` | S7 | T010 |
| Collision check | R5, FR-012 | T017 |
| Record before create (isolation file) | S1 | T034 |
| Unrecorded file at the isolation path refused | S1 | T035 |
| Header check before rewrite | S1 | T036 |
| Symbolic-link refusal of `.wtenv/` | S1 | T037 |
| Isolation file excluded from `git status` | S1 | T038 |
| Verification removes only the file just written | S1 | T039 |
| Header check immediately before unlink (`up`) | S2 | T043 |
| `removing` recorded before unlink | S2 | T044 |
| `is_tracked` in adoption (`up`) | S3 | T048 |
| Adoption never writes or deletes | S3 | T049 |
| FR-033 exec checks 1–7 | S5 | T053–T059 |
| `COMPOSE_PROJECT_NAME` not injected | S5 | T060 |
| Exact-path acceptance in `override_problem` | S4 | T067 |
| `is_tracked` in the teardown outcome | S3 | T068 |
| Header check before delete (teardown) | S4 | T069 |
| Only `ComposeRecord.override` is deleted | FR-002 | T070 |
| FR-027 rows: external, fixed name, device, outside bind | S6, R4 | T080–T083 |
| `<project>_` prefix rule (reading R8) | S6 | T089 |

## Requirement coverage

Every requirement of spec.md maps to at least one task. Checked against FR-001…FR-033 and
SC-001…SC-009: none is uncovered.

| Requirement | Tasks |
|-------------|-------|
| FR-001 | T025, T026, T027, T032, T033, T072 |
| FR-002 | T032, T033, T041, T065, T066, T070, T072 |
| FR-003 | T029, T030, T032, T033 |
| FR-004 | T051, T052, T062, T095 |
| FR-005 | T062, T063 |
| FR-006 | T026, T046, T047, T048, T049, T065, T066, T068 |
| FR-007 | T025, T026, T027, T041, T042 |
| FR-008 | T002, T032, T041, T091 |
| FR-009 | T032–T039, T041, T065–T069 |
| FR-010 | T019, T020 |
| FR-011 | T015, T016, T095 |
| FR-012 | T015, T016, T017, T019, T029, T085 |
| FR-013 | T019, T020, T086 |
| FR-014 | T022, T023 |
| FR-015 | T016, T019, T029 |
| FR-016 | T019 |
| FR-017 | T012, T013 |
| FR-018 | T012, T013, T075, T076 |
| FR-019 | T085, T086 |
| FR-020 | T008, T012, T075, T085 |
| FR-021 | T078, T079, T085 |
| FR-022 | T085, T086 |
| FR-023 | T088, T089 |
| FR-024 | T085, T086 |
| FR-025 | T095 |
| FR-026 | T078, T079, T085 |
| FR-027 | T078–T083, T085 |
| FR-028 | T002, T012, T019, T091, T092 |
| FR-029 | T002, T008, T009, T010 |
| FR-030 | T010, T017, T034–T039, T043, T044, T048, T049, T053–T060, T067–T070, T080–T083, T089, T091 |
| FR-031 | T003, T004, T005, T006 |
| FR-032 | T003, T006, T062, T091 |
| FR-033 | T051–T061 |
| SC-001 | T102 |
| SC-002 | T019, T102 |
| SC-003 | T085, T102 |
| SC-004 | T088, T102 |
| SC-005 | T072, T102 |
| SC-006 | T091, T093, T102 |
| SC-007 | T065, T072, T088, T102 |
| SC-008 | T015, T102 |
| SC-009 | T091, T102 |

## Notes

- Total: 103 tasks (T001–T103).
- plan.md's "Existing tests whose expected outcome this feature changes" names three tests; the
  code has five that assert the v0.1 refusal. The maintainer chose (2026-10-10) to rewrite all
  five (T025, T032). plan.md is not edited by this task list.
- The contract tests move from 001's contract to 002's (T003); 002's `json_models.py` is the full
  schema-1 contract, so every 001 model is still checked.
