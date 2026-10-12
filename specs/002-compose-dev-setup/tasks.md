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
- **⚠RELAX(001:FR-030)**, **⚠RELAX(001:FR-087 outcome)** and **⚠RELAX(001:FR-056, FR-057)** mark
  the tasks that relax a spec 001 requirement (spec, "Relationship to spec 001"; plan.md, "Relaxations of spec 001,
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
(imports only `errors`, `identity` and `registry`, never `compose`, `provision` or `database`;
plan.md, Structure Decision).

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
- **Existing tests.** No existing test changes its expected outcome, except the six named in
  T025, T032 and T106, the two named in T033 (below), the code-count tests in `tests/contract/test_exit_statuses.py` and
  `tests/unit/test_errors.py`, two block-size tests, and one text-output test (all below). Six, not the three of plan.md's table: the maintainer decided on 2026-10-10
  to rewrite the five of T025 and T032, and in the analysis fixes to rewrite the sixth (T106, O2). An existing test may be *extended* (more cases, more assertions) where a task
  says so. If any other existing test starts failing, stop and ask. Do not edit it.
  - **Tests of `check_override_files` (T033).** `test_the_recorded_override_passes` and
    `test_no_override_file_passes` in `tests/unit/test_compose_checks.py` keep their expected
    outcomes (no refusal) but call `compose.override_inventory` instead. T033 removes
    `check_override_files`, which only these two tests still called. The maintainer decided this
    on 2026-10-10, after T025 left them on the old function. The old bodies are shown failing
    against the new code before they are rewritten.
  - **Code-count tests (T003, T004; done).** `tests/contract/test_exit_statuses.py` and
    `tests/unit/test_errors.py` assert the number of error codes, so adding codes 20–23 (FR-031)
    changes their outcome; they are rewritable. The maintainer accepted (2026-10-10) that the
    edits went beyond a count change: `test_exit_statuses.py` gained a helper and a renamed test
    because T003 must read both `cli.md` tables, and `test_errors.py` gained
    `test_the_nineteen_codes_of_spec_001_keep_their_statuses`, which protects FR-031 ("existing
    codes keep their meaning").
  - **Block-size tests (T020; done).** `test_more_ports_than_the_block_holds_is_config_invalid_on_a_first_up`
    and `…_on_a_provisioned_worktree` in `tests/integration/test_us3_compose.py` keep their
    assertions (`config_invalid`, exit 3, `min_block_size == 4`). Only their helper `many_ports()`
    changed, from `"{9000+n}:80"` to `"{9000+n}:{80+n}"`: the old fixture published container port
    80 three times from one service, which spec 002 now refuses with `port_variable_conflict`
    (the FR-028 exception; the collision check of step 5 runs before the block-size check of
    step 6). The maintainer chose this on 2026-10-10; the refusal itself is tested by
    `test_a_name_collision_changes_nothing[one container port published twice-…]`.
  - **Text-output test (T022; done).** `test_the_text_output_shows_the_published_ports_and_the_compose_project`
    in `tests/integration/test_us3_compose.py` changes one substring, `(CACHE_PORT)` to
    `(CACHE_PORT, WTENV_PORT_CACHE_6379_TCP)`: spec 002 FR-014 appends the port variable to the
    `published` text of `up`. Its other assertions are unchanged. The maintainer approved this on
    2026-10-10.
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

- [X] T001 Confirm the baseline: run G5 on the tip of `002-compose-dev-setup` (the commit that adds this file), changing no file in `src/` or `tests/`
  - Nothing is edited. If a gate fails, stop and ask: the failure predates spec 002.
  - **Done when** G5 prints `G5 PASSED` and `git status --porcelain` prints nothing.
- [X] T002 Capture v0.1.1's outputs with v0.1.1's own code, in `tests/fixtures/v0_1_1/make_golden.py`, `tests/fixtures/v0_1_1/configs/`, and `tests/fixtures/v0_1_1/golden/`
  - FR-008, FR-028, FR-029. The golden files are produced by v0.1.1 (tag `v0.1.1`), never written by
    hand and never by the new code.
  - `configs/`: one `.toml` per configuration the v0.1.1 test suite uses, copied verbatim:
    `tests/fixtures/sample_app/wtenv.toml`; `TOML`, `REMAPPED_TOML`, `QUIET_TOML`, `NO_COMPOSE_TOML`
    and `MOVED_TOML` of `tests/integration/test_us3_compose.py`; `SQLITE_TOML` and `COMPOSE_TOML` of
    `tests/integration/test_us1_ports_env.py` and `test_us4_lifecycle.py`; a Postgres configuration
    with `{env:PGPASSWORD}`. For every compose configuration, `configs/<name>.compose.json` holds
    the `docker compose config --format json` output of the compose file it uses, so the script
    needs no Docker.
  - Also three compose configurations written for this task, one per setup of the FR-028
    exception, each with its compose file and `.compose.json` made the same way. v0.1.1 accepts
    all three, so their goldens come from v0.1.1 like the others: `dup_port` (service `web`
    publishes container port 80/tcp on two host addresses, as `tests/unit/test_ports_published.py`
    does); `colliding_names` (services `my-api` and `my_api`, each publishing 8000/tcp); and
    `ports_named_like_a_variable` (a `ports` entry `WTENV_PORT_WEB_80_TCP` beside service `web`
    publishing 80/tcp).
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

- [X] T003 [P] Point the contract tests at spec 002's contract and add failing member checks, in `tests/contract/conftest.py`, `tests/contract/test_models_match_contract.py`, and `tests/contract/test_exit_statuses.py`
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
- [X] T004 [P] Add failing tests for codes 20–23 to `tests/unit/test_errors.py`
  - FR-031: 23 codes, each with exactly one status, no status used twice, the four new ones as in
    T003; `wtenv.errors` still imports only the standard library.
  - **Done when** `uv run pytest tests/unit/test_errors.py -q` exits 1 with assertion failures only.
- [X] T005 Add the four codes and their exit statuses to `src/wtenv/errors.py`
  - FR-031; plan.md, "Error codes, exit statuses, warnings and reasons". No existing code or status
    changes.
  - **Done when** `uv run pytest tests/unit/test_errors.py -q` exits 0.
- [X] T006 Port the 002 members of `json_models.py` into `src/wtenv/output.py`
  - FR-031, FR-032. Models and enums only; no command emits them yet. New fields default to `None`
    or an empty list, so every v0.1.1 document still validates.
  - **Done when** `uv run pytest tests/contract -q` exits 0.
- [X] T007 Group check 2A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(contracts): add spec 002 error codes, warnings and models`.

### 2B. Registry: one optional field (`registry.py`)

- [X] T008 Add failing tests for `ComposeRecord.repository_override` to `tests/unit/test_registry_models.py` and `tests/unit/test_registry_store.py`
  - FR-029; data-model.md, Registry; plan.md S7.
  - New `test_a_v0_1_1_registry_is_saved_byte_for_byte`: load `tests/fixtures/v0_1_1/golden/registry.json`
    (T002) and save it to a temporary state directory. The bytes are identical.
  - An entry with `repository_override=None` serialises with no `repository_override` key. An entry
    with it set round-trips. `REGISTRY_VERSION` is still 1.
  - Kept, and must still pass: `test_a_password_cannot_be_stored_in_a_database_record` (FR-020),
    `test_a_volumes_list_added_to_the_compose_record_makes_the_registry_unreadable` (001:FR-070).
  - **Done when** the two files fail under the failing-test rule.
- [X] T009 Add `repository_override: str | None = Field(default=None, exclude_if=lambda value: value is None)` to `ComposeRecord` in `src/wtenv/registry.py`
  - FR-029. The consistency rule ("set if and only if `override` is
    `.wtenv/compose.isolation.yaml`") is **not** checked by the model, so that a hand-edited
    registry is reported rather than unreadable (data-model.md).
  - **Done when** `uv run pytest tests/unit/test_registry_models.py tests/unit/test_registry_store.py -q` exits 0.
- [X] T010 Mutation check: `exclude_if` on the new field, in `src/wtenv/registry.py` (restored afterwards)
  - Guard: serialise only when set (S7). Remove `exclude_if`; `test_a_v0_1_1_registry_is_saved_byte_for_byte` must fail.
  - **Done when** as in Working rules, "Mutation check".
  - Recorded (2026-10-10): with `exclude_if` removed, `tests/unit/test_registry_store.py::test_a_v0_1_1_registry_is_saved_byte_for_byte`
    fails (exit 1; first assertion line `assert registry_path().read_bytes() == golden`), and so do
    `test_the_example_loads_and_dumps_back_to_equal_json`,
    `test_an_entry_with_no_repository_override_is_dumped_with_no_such_key` and
    `test_a_repository_override_is_saved_and_loads_back_and_only_where_it_is_set`. After restoring the file
    from a backup, SHA-256 `1c2f0af21e3b…48838c3` before and after, `cmp` identical, the two files' tests pass (116).
- [X] T108 Check the downgrade path with v0.1.1's own `registry.load`, in `tests/fixtures/v0_1_1/check_downgrade.py` (new)
  - FR-029; spec, Assumptions, "Downgrade"; data-model.md, Registry; analysis D1.
  - With the new code (after T009), save two registries through `registry.save`, each under its
    own `XDG_STATE_HOME`: `$SCRATCH/down-auto` (the entries of the T002 golden registry, all in the
    automatic arrangement) and `$SCRATCH/down-exec` (the same plus one compose entry with
    `repository_override` set). Then, in a detached `v0.1.1` worktree made as in T002, run
    `XDG_STATE_HOME=<dir> uv run --project "$SCRATCH/v0.1.1" python -I tests/fixtures/v0_1_1/check_downgrade.py`
    for each directory. The script calls v0.1.1's `registry.load` and prints `loaded`, or the
    error code and `details.reason`, then the SHA-256 of `registry.json` before and after.
  - **Done when** the script prints `loaded` for `down-auto` and `registry_unreadable` for
    `down-exec`, the two SHA-256 values are equal for both, `uv run ruff check tests/fixtures/v0_1_1`
    exits 0, and the output is recorded under this task.
  - Recorded output (run 2026-10-10 with the commands above; the `v0.1.1` worktree was removed afterwards):

    ```text
    == down-auto (v0.1.1's registry.load)
    loaded
    sha256 before: 04c6e079e9026bf5cb99e133fc626794ff9f154377098d10372bb37efab01083
    sha256 after:  04c6e079e9026bf5cb99e133fc626794ff9f154377098d10372bb37efab01083
    == down-exec (v0.1.1's registry.load)
    registry_unreadable (reason: invalid_schema)
    sha256 before: 3ec49334ffcf1ecdc0be6f3f1b12d165645210d41d5ed799d3809a8267aa7f08
    sha256 after:  3ec49334ffcf1ecdc0be6f3f1b12d165645210d41d5ed799d3809a8267aa7f08
    ```
- [X] T011 Group check 2B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(registry): record a repository-owned override when one is used`.

### 2C. Configuration: `[database] type = "compose"` (`config.py`)

- [X] T012 [P] Add failing configuration tests to `tests/unit/test_config_database.py`
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
- [X] T013 Accept `type = "compose"`, `service`, and the compose URL rules in `src/wtenv/config.py`
  - FR-017, FR-018. One database mode per worktree. No other setting is added (FR-063 extended
    only by these keys).
  - **Done when** `uv run pytest tests/unit/test_config_database.py tests/unit/test_config.py tests/unit/test_config_compose.py -q` exits 0.
- [X] T014 Group check 2C: runs G5 from the repository root; edits no file
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

- [X] T015 [P] [US2] Add failing tests for the rule and collisions to `tests/unit/test_port_variables.py`
  - FR-011, FR-012, SC-008; research.md R5; files.md, "Env file section".
  - New `test_the_variable_name_depends_only_on_service_port_and_protocol`: the 15 worked examples
    of R5 verbatim (`adminer` → `WTENV_PORT_ADMINER_8080_TCP` … `a-b`/`a--b` distinct); the rule
    is "upper case with every character other than `A–Z` and `0–9` replaced by `_` (one `_` per
    character; nothing is trimmed or collapsed)". The name is the same for any block, host port,
    host IP and service order.
  - Collisions → `port_variable_conflict` (exit 22), `details.variable` the name, `details.sources`
    two strings of the forms `service <name>, container port <n>/<protocol>`, `wtenv.toml ports`,
    or `DATABASE_URL`: `my-api` and `my_api`; `API` and `api`; `web` 6379/tcp published twice
    (E1.6b); a name equal to a `ports` entry. `DATABASE_URL` is in the set of configured names
    when `[database]` is set, as data, with no special case in the code; but every derived name
    starts with `WTENV_PORT_`, so none can equal it. The test asserts that property and that the
    set includes `DATABASE_URL`, instead of a collision case that cannot occur (maintainer,
    2026-10-10).
  - SC-008: for every compose model under `tests/` (the `.compose.json` files of T002 and every
    `STACK` constant of `tests/integration/test_us3_compose.py`), either all names are distinct or
    the check raises. Never one name for two ports.
  - **Done when** the file fails under the failing-test rule.
- [X] T016 [US2] Implement `port_variable_name()` and `check_port_variables()` in `src/wtenv/ports.py`
  - FR-011, FR-012, FR-015: pure functions; no port is assigned for a variable.
  - **Done when** `uv run pytest tests/unit/test_port_variables.py -q` exits 0.
- [X] T017 [US2] Mutation check: the collision check, in `src/wtenv/ports.py` (restored afterwards)
  - Guard: "two different published ports MUST never share a variable name" (FR-012). Make
    `check_port_variables` return without checking; the collision cases of T015 must fail.
  - **Done when** as in Working rules, "Mutation check".
  - Recorded (2026-10-10): with `return` as the first statement of `check_port_variables`'s body,
    `tests/unit/test_port_variables.py` fails (exit 1; first assertion line `Failed: DID NOT RAISE
    WtenvError`): 8 tests, namely `test_service_names_that_differ_only_in_punctuation_collide`,
    `…_in_case_collide`, `test_one_container_port_published_twice_collides`,
    `test_a_name_equal_to_a_ports_entry_collides`, `test_an_error_message_names_the_variable_and_both_sources`
    and the `colliding_names`, `dup_port` and `ports_named_like_a_variable` cases of
    `test_every_compose_model_gives_distinct_names_or_is_refused`. After restoring `ports.py` from a
    backup (the file holds uncommitted work, so `git checkout` cannot restore it), SHA-256
    `bd862de80053…975d3ba397` before and after, `cmp` identical, 38 tests pass.
- [X] T018 [US2] Group check 3A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(ports): derive a variable name for every published port`.

### 3B. The variables in the env file and in `exec` (`provision.py`)

- [X] T019 [US2] Add failing integration tests to `tests/integration/test_us2_port_variables.py` (new; `pytestmark = pytest.mark.integration`; Docker)
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
- [X] T020 [US2] Write the port variables in `_variables` and check them in `_compose_plan`, in `src/wtenv/provision.py`
  - FR-010, FR-012, FR-013. The collision check runs in step 5, before step 8 (cli.md, `wtenv up`).
    `exec` needs no change: it reads the section (001:FR-056).
  - **Done when** `uv run pytest tests/integration/test_us2_port_variables.py tests/integration/test_us3_compose.py -q` exits 0.
- [X] T021 [US2] Group check 3B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): write a variable for every published port`.

### 3C. Showing the variables in `up` and `ls` (`listing.py`, `output.py`)

- [X] T022 [P] [US2] Add failing tests for the views to `tests/unit/test_render_up.py`, `tests/unit/test_render_ls.py`, and `tests/integration/test_us2_port_variables.py`
  - FR-014; User Story 2 scenario 7; cli.md, `wtenv up` text and `wtenv ls`.
  - `PortView.port_variable` is set for every published port and `None` for a `ports` variable.
    `up` text: `backend:8000 -> 20002 (WTENV_PORT_BACKEND_8000_TCP)`; a tied port:
    `db:5432 -> 20011 (DB_PORT, WTENV_PORT_DB_5432_TCP)`. `ls` text cell:
    `service:container-port->host-port (NAME)`. JSON of `up` and `ls` carries `port_variable`.
  - Existing render tests build views without `port_variable` and must pass unchanged.
  - This existing test changes with the output format:
    `test_the_text_output_shows_the_published_ports_and_the_compose_project` in
    `tests/integration/test_us3_compose.py` (one substring; see Working rules).
  - **Done when** the new cases fail under the failing-test rule.
- [X] T023 [US2] Set `port_variable` in `listing.port_views` and render it in `src/wtenv/listing.py` and `src/wtenv/output.py`
  - **Done when** `uv run pytest tests/unit/test_render_up.py tests/unit/test_render_ls.py tests/integration/test_us2_port_variables.py -q` exits 0.
- [X] T024 [US2] Group check 3C: runs G5 from the repository root; edits no file
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

- [X] T025 [US1] ⚠RELAX(001:FR-030) Rewrite four existing tests that assert the v0.1 refusal, in `tests/unit/test_compose_checks.py`
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
- [X] T026 [P] [US1] Add failing tests for the inventory to `tests/unit/test_override_inventory.py` (new)
  - FR-001, FR-006 (classification only), FR-007; data-model.md, "Override inventory (step 5)".
    Every row of the classification table: `wtenv_own`; `adopt` (recorded path, no header, git
    tracks it, using a real `git init` in `tmp_path`); `ownership_conflict` (recorded path, no
    header, untracked; details `kind` `compose_override`, `name` the path); `repository_owned`
    (any other of the four names, header-less); `ownership_conflict` for an unrecorded regular
    file at another of the four names that starts with wtenv's header (as v0.1.1; data-model.md,
    row "header present").
  - Counts: 0 → `automatic`; 1 → `exec_only`, every `wtenv_own` file marked for removal; ≥ 2 →
    `override_ambiguous` (exit 21), `details.files` absolute and sorted, every file unchanged.
  - A symbolic link at the recorded path is refused first (001:FR-086, unchanged). A link at an
    unrecorded override name is `repository_owned`, and its target is not read for the header
    (spec, Edge cases; O2).
  - **Done when** the file fails under the failing-test rule.
- [X] T027 [US1] Implement `override_inventory()` and the arrangement result in `src/wtenv/compose.py`
  - Read only: it writes, renames and deletes nothing. `check_override_files` stays until T033
    switches `up` to the inventory.
  - **Done when** `uv run pytest tests/unit/test_override_inventory.py tests/unit/test_compose_checks.py -q` exits 0.
- [X] T147 [US1] Mutation check: an unrecorded override file that starts with wtenv's header is `ownership_conflict`, in `src/wtenv/compose.py` (restored afterwards)
  - Guard: data-model.md, Override inventory, row "any other of the four names, a regular file,
    header present" (analysis C3; safety review, item 8). Classify that file as
    `repository_owned`; its case in `tests/unit/test_override_inventory.py` (T026) must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T028 [US1] Group check 4A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(compose): classify override files beside the compose file`.

### 4B. Resolving the compose file with the repository override (`compose.py`)

- [X] T029 [P] [US1] Add failing tests for the combined model to `tests/unit/test_compose_model.py` and `tests/unit/test_compose_checks.py`
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
- [X] T030 [US1] Accept the repository override in `resolution_command` and `resolve_model`, and add the `:` check, in `src/wtenv/compose.py`
  - **Done when** `uv run pytest tests/unit/test_compose_model.py tests/unit/test_compose_checks.py -q` exits 0.
- [X] T146 [US1] Mutation check: the `compose_path_separator` refusal of a path that contains `:`, in `src/wtenv/compose.py` (restored afterwards)
  - Guard: plan.md, "Error codes", `unsupported` reason `compose_path_separator` (research R2;
    safety review, item 8). Remove the check; the `:` cases of T029 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T031 [US1] Group check 4B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(compose): resolve the compose file together with the repository override`.

### 4C. Safety group — writing the isolation file (`compose.py`, `provision.py`, `isolation.py`; plan.md S1, S4)

- [X] T104 [US1] Add failing tests for the exact isolation path in `override_problem` to `tests/unit/test_compose_checks.py`
  - FR-009; plan.md S4; analysis O1. Comes before T033, which needs it.
  - `override_problem(root, ".wtenv/compose.isolation.yaml")` returns `None` when the file is
    missing or starts with wtenv's header, and a problem when it lacks the header; so
    `check_recorded_override` passes the first two and raises `ownership_conflict` for the third.
  - Always a problem (left alone), whatever the contents: `.wtenv/notes.yaml`,
    `.wtenv/sub/compose.isolation.yaml`, `../.wtenv/compose.isolation.yaml`, and
    `compose.isolation.yaml` at the root. The four override names keep their v0.1.1 cases.
  - **Done when** the new cases fail under the failing-test rule (the isolation-path cases fail on
    the assertion that the result is `None`).
- [X] T105 [US1] Accept exactly `.wtenv/compose.isolation.yaml` in `override_problem`, in `src/wtenv/compose.py` and `src/wtenv/isolation.py` (new)
  - FR-009; analysis O1, U2. `isolation.py` starts here with two constants: `OVERRIDE_HEADER`
    (wtenv's header line, moved from `compose._OVERRIDE_HEADER`) and
    `ISOLATION_FILE = ".wtenv/compose.isolation.yaml"`. `compose.py` imports both from it.
    `isolation.py` imports only `errors`, `identity` and `registry`.
  - `override_problem` accepts the four names **or exactly** `ISOLATION_FILE`; nothing else under
    `.wtenv/`. No caller changes.
  - **Done when** `uv run pytest -m "not integration" -q` exits 0 and
    `grep -n '_OVERRIDE_HEADER = ' src/wtenv/compose.py` prints nothing.
- [X] T032 [US1] ⚠RELAX(001:FR-030) Add failing tests for the exec-only arrangement in `up`, in `tests/integration/test_us1_repository_override.py` (new), `tests/integration/test_us3_compose.py`, `tests/integration/test_recovery.py`, `tests/unit/test_compose_write.py`, and `tests/unit/test_isolation.py` (new)
  - Rewrite the fifth test, `tests/integration/test_us3_compose.py::test_a_developers_override_is_an_ownership_conflict_and_is_not_modified`,
    in place → exit 0, the registry records the exec-only arrangement (`override` is
    `.wtenv/compose.isolation.yaml`, `repository_override` is the committed override's path), the
    committed override byte-identical (SHA-256 before and after), `.wtenv/compose.isolation.yaml`
    written (FR-001, FR-002). The reported fields `compose_isolation` and
    `compose_repository_override` are asserted in 4G (T062), not here. Rename it to
    `test_a_developers_override_is_used_through_exec_and_is_not_modified`, with the old name in
    its docstring.
  - New, in `test_us1_repository_override.py`, one per scenario of User Story 1: 1 (exit 0,
    override byte-identical, new `test_git_status_is_clean_after_up_in_the_exec_only_arrangement`);
    2 and 8 (the override publishes `backend` on fixed host port 8000 and sets `command`;
    `docker compose config`, run in the worktree with exactly the environment
    `isolation.compose_environment(root, record)` returns, passed as the subprocess environment,
    shows a block port and the override's command, FR-003; the test never sets `COMPOSE_FILE` or
    `COMPOSE_PATH_SEPARATOR` itself); 4 (a repeat `up` gives identical ports, project and isolation-file
    bytes); 6 (no repository override: v0.1.1's override name and bytes, compared with
    `tests/unit/golden/compose.override.yaml`, FR-008).
  - R1 pin, new `test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose`:
    two-service `valkey` stack, repository override with fixed ports; started with
    `docker compose up -d`, run in the worktree with exactly the environment
    `isolation.compose_environment(root, record)` returns (the test sets neither variable
    itself); `docker ps` for the project shows only block ports. T051/T052 add the `wtenv exec`
    versions; T072 covers scenarios 2 and 3 through `wtenv exec`.
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
    and `COMPOSE_PATH_SEPARATOR=:` with those exact values, every key it returns is within
    `{COMPOSE_FILE, COMPOSE_PATH_SEPARATOR, COMPOSE_ENV_FILES}` (T052/T120 add the last one in
    4F, so the key set is not asserted to equal the first two), and never `COMPOSE_PROJECT_NAME`
    (data-model.md, Isolation environment).
  - **Done when** these files fail under the failing-test rule.
- [X] T033 [US1] ⚠RELAX(001:FR-030) Write and verify the isolation file in the exec-only arrangement, in `src/wtenv/isolation.py`, `src/wtenv/provision.py`, and `src/wtenv/compose.py`
  - FR-001, FR-002, FR-003, FR-009; cli.md, `wtenv up` steps 5, 8, 11; data-model.md,
    Transitions rows "none → `automatic`" and "none → `exec_only`".
  - `_compose_plan` uses `override_inventory` (remove `check_override_files` and its last caller).
    In the exec-only arrangement: the isolation-path checks of cli.md step 5; `_registry_step`
    records `override = .wtenv/compose.isolation.yaml` (`creating`) and `repository_override`
    before step 11, and adds `/.wtenv/compose.isolation.yaml` to `exclude_patterns`;
    `_compose_step` rechecks the header immediately before the write and calls
    `write_and_verify_override` with the environment from `isolation.compose_environment`.
  - Needs T105: without it `override_problem` refuses `.wtenv/compose.isolation.yaml`, so
    `check_recorded_override` stops the rewrite.
  - `isolation.py` (created in T105, with the header line and the isolation path): add
    `compose_environment` only in this task (the `exec` checks come in T052). It imports only
    `errors`, `identity` and `registry`. The link rule of `_check_override_links` changes in T106.
  - The repository override's path is never passed to a write, unlink, chmod or exclude call
    (FR-002).
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_us3_compose.py tests/integration/test_recovery.py tests/unit/test_compose_write.py tests/unit/test_isolation.py -q` exits 0.
- [X] T106 [US1] Accept a symbolic link at a repository override name in `_check_override_links`, test first, in `tests/integration/test_us1_ports_env.py`, `tests/integration/test_us1_repository_override.py`, and `src/wtenv/provision.py`
  - FR-001, FR-002, FR-009; spec, Edge cases, "The repository's override is a symbolic link";
    data-model.md, Override inventory; plan.md S1; analysis O2. Needs T033 (`up` uses the
    inventory). Two steps, in order.
  - Step 1, failing tests (failing-test rule; result recorded under this task):
    - New `test_a_link_at_an_unrecorded_override_name_is_the_repository_override`: an untracked
      `compose.override.yaml` that links to a valid override in `decoy` → exit 0,
      the registry records the exec-only arrangement (`override` is the isolation path,
      `repository_override` is the link's path); the link and
      `snapshot_tree(decoy)` unchanged; the link's path is not in the exclude block.
    - New `test_a_link_at_a_recorded_override_path_is_refused_in_both_arrangements`: after an
      automatic `up`, `compose.override.yaml` replaced by a link; after an exec-only `up`,
      `.wtenv/compose.isolation.yaml` replaced by a link. Each link targets a file in `decoy` that
      starts with wtenv's header, so a header read through it would pass. `up` → exit 7,
      `env_file_unusable`, `symlink`; decoy, registry bytes and env file unchanged.
    - New `test_a_link_at_the_isolation_path_is_refused_on_a_first_up`: a repository override
      and an unrecorded link at `.wtenv/compose.isolation.yaml` → exit 7, `env_file_unusable`,
      `symlink`; decoy unchanged; no registry.
    - Rewrite the sixth existing test, `tests/integration/test_us1_ports_env.py::test_a_compose_override_that_is_a_link_is_refused_on_a_first_up_too`
      (an untracked, dangling link at `compose.override.yaml` on a first `up`), in place → the
      link is the repository's override, so `up` fails at resolution with `config_invalid`
      (exit 3; Compose cannot read the file), not `env_file_unusable`. The dangling target is
      not created, `snapshot_tree(decoy)` and the link are unchanged, and no registry exists.
      Rename it `test_a_dangling_link_at_an_unrecorded_override_name_is_never_followed_for_writing`,
      with the old name in its docstring.
    - Kept unchanged: `test_a_compose_override_that_is_a_link_is_refused_before_anything_changes`,
      `test_removing_compose_does_not_remove_an_override_that_is_a_link`,
      `test_a_new_compose_file_does_not_remove_the_old_override_through_a_link`, and
      `tests/integration/test_us4_lifecycle.py::test_down_reports_a_compose_override_that_is_a_link_as_failed_and_leaves_its_target_alone`
      (all are links at a recorded path).
  - Step 2, the change in `_check_override_links`: drop the unconditional
    `_refuse_symlink(root, new_override)`. Refuse a link at `new_override` only when it is the
    recorded path. Keep `_refuse_symlink` on every recorded path, the same as `new_override` or
    not. In the exec-only arrangement, refuse a link at `.wtenv/` and at `ISOLATION_FILE` (T033's
    step-5 check). The link check runs before `override_problem`, so no header is read through a
    link. A recorded isolation path that differs from `new_override` is rewritten or removed, so
    its form, link and header checks stay (T105 accepts the path). Update the docstring.
  - **Done when** the Step 1 failure is recorded under this task, and
    `uv run pytest tests/integration/test_us1_ports_env.py tests/integration/test_us1_repository_override.py -q` exits 0.
- [X] T034 [US1] Mutation check: record before create (001:FR-067), in `src/wtenv/provision.py` (restored afterwards)
  - Move the `_registry_step` save of the isolation record after the write; the exec-only case of
    `test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T035 [US1] Mutation check: an unrecorded file at the isolation path is refused, in `src/wtenv/provision.py` (restored afterwards)
  - Remove the check; `test_an_unrecorded_file_at_the_isolation_path_is_an_ownership_conflict_and_is_kept` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T036 [US1] Mutation check: header recheck before rewriting the isolation file (001:FR-087), in `src/wtenv/provision.py` (restored afterwards)
  - Remove the recheck immediately before the write; the exec-only case of
    `test_up_does_not_rewrite_an_override_that_lost_the_header_after_the_plan` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T037 [US1] Mutation check: no symbolic link at `.wtenv/` or the isolation file (001:FR-086), in `src/wtenv/provision.py` (restored afterwards)
  - Remove `_refuse_symlink(root, ".wtenv/compose.isolation.yaml")`;
    `test_a_symlinked_wtenv_directory_is_refused_in_the_exec_only_arrangement` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T038 [US1] Mutation check: the isolation file is excluded from `git status` (001:FR-018), in `src/wtenv/provision.py` (restored afterwards)
  - Drop the exclude pattern; `test_git_status_is_clean_after_up_in_the_exec_only_arrangement` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T039 [US1] Mutation check: verification removes only the file just written (001:FR-033), in `src/wtenv/compose.py` (restored afterwards)
  - Skip the removal on verification failure; the isolation-file case of
    `test_a_replaced_override_that_fails_verification_is_removed_too` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T107 [US1] Mutation check: a link at a recorded override path is still refused (001:FR-086, FR-009), in `src/wtenv/provision.py` and `src/wtenv/compose.py` (restored afterwards)
  - Guard: after T106, a link at every recorded override path is still refused. **Defence in
    depth: three layers refuse a link at a recorded path, and the mutation removes all three
    together.** The layers are the two recorded-path `_refuse_symlink` calls in
    `_check_override_links`, the link refusal in `override_inventory` (`compose.py`), and the
    `_refuse_symlink(root, ISOLATION_FILE)` call in `_check_isolation_path`. Every layer stays in
    the code. With one or two removed, the other layers still refuse (T107 found this: removing
    the first alone fails no test). With all three removed, both cases of
    `test_a_link_at_a_recorded_override_path_is_refused_in_both_arrangements` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T122 [US1] Add failing tests for the excluded-override warning to `tests/integration/test_us1_repository_override.py` and `tests/integration/test_gitutil_git.py`
  - FR-002 (amended by safety finding M3); cli.md, `wtenv up` step 5; files.md,
    `.git/info/exclude` block; plan.md, Decisions to review 1.
  - New `test_an_untracked_repository_override_ignored_through_wtenvs_block_is_warned`: worktree
    A in the automatic arrangement (its `up` adds `/compose.override.yaml` to wtenv's block);
    worktree B of the same repository with an untracked `compose.override.yaml` of its own.
    `up` in B: exit 0, the registry records the exec-only arrangement (`override` is the isolation
    path, `repository_override` is B's override path), warning `compose_override_excluded` with
    `details.file` B's override (absolute) and `details.pattern` `/compose.override.yaml`; B's
    override byte-identical; no line of `.git/info/exclude` removed.
  - Not warned: the same file when git tracks it; an untracked override matched only by a line
    outside wtenv's block (the developer's own exclude or `.gitignore` line); an untracked
    override that no line matches.
  - **Done when** the new cases fail under the failing-test rule.
- [X] T123 [US1] Warn `compose_override_excluded` in the exec-only `up`, in `src/wtenv/gitutil.py`, `src/wtenv/provision.py`, and `src/wtenv/output.py`
  - Needs T033. In step 5, after the inventory, when the repository override is untracked:
    `git check-ignore -v --no-index -- <path>`; the warning only when the reported source is
    the repository's `info/exclude` and the reported line lies inside wtenv's block markers.
    Read only: no file and no exclude line changes. The warning code is the member T006 ported
    from `json_models.py`.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_gitutil_git.py -q`
    exits 0 and `grep -n 'COMPOSE_OVERRIDE_EXCLUDED' src/wtenv/output.py` prints a line.
- [X] T124 [US1] Mutation check: the excluded-override warning, in `src/wtenv/provision.py` (restored afterwards)
  - Guard: FR-002, safety finding M3. Skip the `check-ignore` call;
    `test_an_untracked_repository_override_ignored_through_wtenvs_block_is_warned` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T040 [US1] Group check 4C: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): write a recorded isolation file beside a repository-owned override`.

### 4D. Safety group — removing wtenv's own override on a change of arrangement (`provision.py`; plan.md S2)

- [X] T041 [US1] Add failing tests for arrangement transitions to `tests/integration/test_us1_repository_override.py` and `tests/integration/test_us3_compose.py`
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
- [X] T042 [US1] Carry out the transitions through the existing path-change branch and `_remove_override`, in `src/wtenv/provision.py`
  - The old record is `removing` before the unlink; the header is rechecked immediately before
    it; the new file is `creating` before it is written. No new unlink call site.
  - Two saves, as data-model.md Transitions says: step 8 saves the old record, fields unchanged,
    as `removing`; step 11 unlinks under the header recheck, then saves the new record (`override`,
    `creating`, and `repository_override` set or cleared in that same save) before the write.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_us3_compose.py -q` exits 0.
- [X] T043 [US1] Mutation check: header recheck immediately before the unlink in `_remove_override` (001:FR-087), in `src/wtenv/provision.py` (restored afterwards)
  - Remove it; the transition cases of `test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T044 [US1] Mutation check: `removing` is recorded before the unlink, in `src/wtenv/provision.py` (restored afterwards)
  - Record `removing` after the unlink; the interrupted-transition case of T041 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [X] T045 [US1] Group check 4D: runs G5 from the repository root; edits no file
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
  - Adoption bypasses the path-change branch: `_compose_record` replaces the record in one step-8
    save (`override` the isolation path, `creating`; `repository_override` the adopted path), and
    never marks the adopted path `removing`, so `_compose_step` finds `override` equal to the
    plan's and never calls `_remove_override` on the adopted file (data-model.md Transitions).
  - **Done when** `uv run pytest tests/unit/test_override_inventory.py tests/integration/test_us1_repository_override.py -q` exits 0.
- [ ] T048 [US1] Mutation check: `is_tracked` in adoption by `up`, in `src/wtenv/compose.py` (restored afterwards)
  - Drop the tracked condition; `test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T049 [US1] Mutation check: adoption never writes or deletes the file, in `src/wtenv/provision.py` (restored afterwards)
  - Make the adoption branch unlink the adopted file directly (`os.unlink`, bypassing
    `_remove_override` and its header check); the byte-identical assertion of T046 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T050 [US1] Group check 4E: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `feat(up): adopt a tracked override at the recorded path as the repository's`.

### 4F. Safety group — environment injection by `exec` and post-up commands (`isolation.py`, `execcmd.py`, `provision.py`; plan.md S5)

- [ ] T051 [US1] ⚠RELAX(001:FR-030) ⚠RELAX(001:FR-056, FR-057) Add failing tests for FR-033 to `tests/unit/test_isolation.py`, `tests/integration/test_us1_exec_isolation.py` (new), and `tests/unit/test_lazy_imports.py`
  - FR-004, FR-033; data-model.md, "`exec` checks (FR-033), in order"; cli.md, `wtenv exec`.
  - New `test_exec_does_not_run_the_command_when_isolation_cannot_apply`, parametrised by the seven
    reasons in table order: `compose_env_override` (`COMPOSE_PROJECT_NAME`, then `COMPOSE_FILE`,
    non-empty; `details.variable`; each also set only in `.env` beside the compose file, after
    `up`, with `details.path` that file); `recorded_value_invalid` (`override` not the isolation path,
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
- [ ] T131 [US1] Add a failing test for post-up commands on a repeat `up` with nothing changed, in `tests/integration/test_us1_exec_isolation.py`
  - FR-033 ("Post-up commands MUST get the same"), FR-010, 001:FR-036; safety finding M6; plan.md
    S5; cli.md, `wtenv up` step 13.
  - New `test_post_up_commands_get_the_isolation_environment_on_a_repeat_up`: an exec-only
    worktree with `post_up = ["env > <file>"]`. `up`, then a second `up` with nothing changed
    (every change `unchanged`), which runs the post-up commands from the early-return branch of
    `provision.up` (the `_run_post_up` call near `provision.py:149` in v0.1.1). The file written
    by the second run has `COMPOSE_FILE` equal to the three absolute paths of data-model.md
    ("Isolation environment"), `COMPOSE_PATH_SEPARATOR=:`, and one `WTENV_PORT_…` line per
    published port, and these lines equal the first run's.
  - **Done when** the new test fails under the failing-test rule.
- [ ] T132 [US1] Build the post-up environment inside `_run_post_up` from the entry, at both call sites, in `src/wtenv/provision.py`
  - Needs T033 (`isolation.compose_environment`). `_run_post_up` takes the entry (and the plan
    for `DATABASE_URL`) and builds its environment itself: the section's variables, plus, in the
    exec-only arrangement, `isolation.compose_environment`. Both call sites, the repeat-`up`
    branch and step 13, pass the entry instead of a variable list, so they cannot differ.
  - **Done when** `uv run pytest tests/integration/test_us1_exec_isolation.py -q -k repeat_up` exits 0.
- [ ] T052 [US1] ⚠RELAX(001:FR-030) ⚠RELAX(001:FR-056, FR-057) Apply the FR-033 checks and the isolation environment in `src/wtenv/isolation.py`, `src/wtenv/execcmd.py`, and `src/wtenv/provision.py`
  - The checks run only when the entry records `repository_override`, after the `provisioned`
    check, and before `os.execvpe`. On failure: `isolation_unavailable`, exit 125, nothing run.
  - `_run_post_up` takes its environment from the same `isolation.compose_environment`.
    `COMPOSE_PROJECT_NAME` is never set.
  - Check 1 reads `.env` beside the compose file with the rule of `compose.check_environment`.
    That assignment pattern and the two variable names move to `isolation.py`, and `compose.py`
    imports them, so the two checks cannot drift. Check 5 uses the header line T105 moved there.
  - **Done when** `uv run pytest tests/unit/test_isolation.py tests/integration/test_us1_exec_isolation.py tests/unit/test_lazy_imports.py -q` exits 0.
- [ ] T053 [US1] Mutation check: FR-033 check 1, `COMPOSE_PROJECT_NAME`/`COMPOSE_FILE` in the calling environment, in `src/wtenv/isolation.py` (restored afterwards)
  - Remove it; the `compose_env_override` case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T109 [US1] Mutation check: FR-033 check 1 also reads `.env` beside the compose file, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: analysis U1. Remove the `.env` read from check 1; the `.env` cases of
    `compose_env_override` (T051) must fail.
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
- [ ] T114 [US1] ⚠RELAX(001:FR-056, FR-057) Add failing tests for check A, an inherited `COMPOSE_FILE` that names another worktree's isolation file, to `tests/integration/test_us1_exec_isolation.py` and `tests/unit/test_isolation.py`
  - FR-033 (amended by safety finding M1); data-model.md, "`exec` checks in every other
    worktree", row A; spec, Edge cases.
  - New `test_exec_refuses_another_worktrees_isolation_file_in_compose_file`: worktree A
    exec-only, worktree B automatic, worktree C without `[compose]`, all provisioned. With
    `COMPOSE_FILE` set to the value `exec` sets in A (as a shell started by `wtenv exec -- sh` in
    A leaves it), `wtenv exec -- touch <sentinel>` in B and in C → exit 125, `error.code`
    `isolation_unavailable`, `details.reason` `compose_env_override`, `details.variable`
    `COMPOSE_FILE`, `details.path` A's isolation file (absolute); the sentinel not created. The
    same with `COMPOSE_PATH_SEPARATOR=';'` and the list joined with `;`.
  - As in v0.1.1 (the command runs, exit 0, `COMPOSE_FILE` passed through unchanged):
    `COMPOSE_FILE` naming B's own compose file; naming a `compose.isolation.yaml` that is not
    in a `.wtenv/` directory; naming `<B root>/.wtenv/compose.isolation.yaml`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T115 [US1] ⚠RELAX(001:FR-056, FR-057) Apply check A in every worktree that is not exec-only, in `src/wtenv/isolation.py` and `src/wtenv/execcmd.py`
  - Needs T052. After the `provisioned` check and before `os.execvpe`; on failure
    `isolation_unavailable`, exit 125, nothing run. The command's environment is unchanged
    (v0.1.1's plus the section). `exec` still does not import `wtenv.compose`.
  - **Done when** `uv run pytest tests/unit/test_isolation.py tests/integration/test_us1_exec_isolation.py tests/unit/test_lazy_imports.py -q` exits 0.
- [ ] T116 [US1] Mutation check: check A, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: FR-033, safety finding M1. Remove it; the refusal cases of
    `test_exec_refuses_another_worktrees_isolation_file_in_compose_file` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T148 [US1] Add failing tests for check A before post-up commands to `tests/integration/test_us1_exec_isolation.py`
  - FR-033 (extended by the maintainer's decision on safety finding M1, plan.md, Decisions to
    review 4); 001:FR-036, FR-037; data-model.md, row A; cli.md, `wtenv up` step 13.
  - New `test_up_runs_no_post_up_command_when_compose_file_names_another_worktrees_isolation_file`:
    worktree A exec-only, worktree C without `[compose]` with `post_up = ["touch <sentinel>"]`.
    With `COMPOSE_FILE` set to the value `exec` sets in A, `wtenv up` in C → exit 20,
    `error.code` `isolation_unavailable`, `details.reason` `compose_env_override`,
    `details.variable` `COMPOSE_FILE`, `details.path` A's isolation file (absolute); the sentinel
    not created; the entry `incomplete`; C's port block and env file left in place. The same for
    a repeat `up` of C with nothing changed (the second call site of `_run_post_up`), and with
    `COMPOSE_PATH_SEPARATOR=';'` and the list joined with `;`.
  - A check A refusal runs no command, so the error is `isolation_unavailable` (exit 20), not
    `post_up_failed`; the entry becomes `incomplete` and the provisioned resources stay in place,
    as 001:FR-037 requires for a stopped post-up. These cases pin that state.
  - As in v0.1.1 (`up` succeeds, the sentinel is created): `COMPOSE_FILE` naming C's own compose
    file; naming a `compose.isolation.yaml` that is not in a `.wtenv/` directory; naming
    `<C root>/.wtenv/compose.isolation.yaml`.
  - In a worktree with `[compose]` (the automatic arrangement), the same `COMPOSE_FILE` makes `up`
    fail with `unsupported` (v0.1.1, unchanged), not `isolation_unavailable`; the sentinel is not
    created.
  - Needs T115 and T132 (the check function and the single place that runs post-up commands).
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T149 [US1] Apply check A in `_run_post_up`, in every worktree, in `src/wtenv/provision.py`
  - FR-033. Needs T115 (the check A function in `src/wtenv/isolation.py`, reused as it is) and
    T132 (`_run_post_up` is the one place both call sites go through). Before the first command,
    when the worktree has at least one post-up command, `_run_post_up` runs check A with the
    worktree root. On failure it sets the entry `incomplete`, as for a failing command, and raises
    `isolation_unavailable` with the details of check A; no command runs. No other check of
    `exec` moves into `up`, and the environment of post-up commands does not change.
  - **Done when** `uv run pytest tests/integration/test_us1_exec_isolation.py tests/unit/test_isolation.py tests/unit/test_lazy_imports.py -q` exits 0.
- [ ] T150 [US1] Mutation check: check A before post-up commands, in `src/wtenv/provision.py` (restored afterwards)
  - Guard: FR-033, the decision on safety finding M1. Remove the check A call from
    `_run_post_up`; the refusal cases of
    `test_up_runs_no_post_up_command_when_compose_file_names_another_worktrees_isolation_file`
    (first `up` and repeat `up`) must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T117 [US1] Add failing tests for the `.env` line forms and `COMPOSE_ENV_FILES` to `tests/unit/test_isolation.py`, `tests/unit/test_compose_checks.py`, and `tests/integration/test_us1_exec_isolation.py`
  - FR-033 (amended by safety finding M2); research R7 (E6.1, E6.3); data-model.md, check 1 and
    "Isolation environment"; spec, Known limits.
  - Check 1 and `up`'s `check_environment`, one case per E6.1 form, for `COMPOSE_PROJECT_NAME`
    and for `COMPOSE_FILE`: `KEY: value`, `KEY:value`, `export KEY: value`, `   KEY : value`,
    a CRLF line end, and a UTF-8 byte-order mark before `KEY=value`. Each →
    `compose_env_override` (`exec`: exit 125, sentinel not run; `up`: `unsupported`, nothing
    changed). Still accepted: `# COMPOSE_PROJECT_NAME: x` and an empty value. Kept unchanged:
    `test_the_variable_in_dot_env_beside_the_compose_file_is_unsupported` and the other `.env`
    tests of `tests/unit/test_compose_checks.py`.
  - `COMPOSE_ENV_FILES` non-empty in the calling environment of an exec-only `exec` →
    `compose_env_override`, `details.variable` `COMPOSE_ENV_FILES`, exit 125, sentinel not run.
  - `isolation.compose_environment` also returns `COMPOSE_ENV_FILES`: the absolute `.env` beside
    the compose file when it exists, else `/dev/null`. Update the T032 case of
    `tests/unit/test_isolation.py` that expects exactly two variables (a test of this feature,
    not one of the v0.1.1 tests the Working rules protect).
  - Integration, run by the CI Compose and by the `minimums` job (2.24.4): after `up` in an
    exec-only worktree, a subdirectory `sub/` whose `.env` sets
    `COMPOSE_PROJECT_NAME=wtenv-test-<random>`; `wtenv exec -- sh -c 'cd sub && docker compose config --format json'`
    reports the recorded project, and a value of the project's `.env` is interpolated (E6.3b).
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T118 [US1] Widen the `.env` pattern, refuse an inherited `COMPOSE_ENV_FILES`, and set `COMPOSE_ENV_FILES`, in `src/wtenv/isolation.py`, `src/wtenv/compose.py`, `src/wtenv/execcmd.py`, and `src/wtenv/provision.py`
  - Needs T052 (the pattern lives in `isolation.py` and `compose.check_environment` imports it).
    The pattern is data-model.md's (`[=:]` and an optional byte-order mark).
    `compose_environment` adds `COMPOSE_ENV_FILES`; `exec`, post-up commands and `up`'s
    verification take it from there. The automatic arrangement's environment does not change.
  - **Done when** `uv run pytest tests/unit/test_isolation.py tests/unit/test_compose_checks.py tests/integration/test_us1_exec_isolation.py tests/integration/test_us3_compose.py -q` exits 0.
- [ ] T119 [US1] Mutation check: the `.env` pattern accepts `:` and a byte-order mark, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: research R7, E6.1. First put back v0.1.1's `=`-only pattern: the `KEY: value` cases
    of T117 must fail. Then, separately, drop only the byte-order-mark part: the byte-order-mark
    case must fail.
  - **Done when** as in Working rules, "Mutation check", for both mutations.
- [ ] T120 [US1] Mutation check: `exec` sets `COMPOSE_ENV_FILES`, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: research R7, E6.3. Remove `COMPOSE_ENV_FILES` from `compose_environment`; the
    subdirectory case of T117 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T121 [US1] Mutation check: an inherited `COMPOSE_ENV_FILES` is refused, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: research R7, E6.3d. Drop `COMPOSE_ENV_FILES` from the variables check 1 reads; its
    case of T117 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T128 [US1] ⚠RELAX(001:FR-056, FR-057) Add failing tests for check B, an override that appeared in the automatic arrangement, to `tests/integration/test_us1_exec_isolation.py`
  - FR-033 (amended by safety finding M5), FR-008; research R7, E6.2; data-model.md, "`exec`
    checks in every other worktree", row B; spec, Edge cases ("appears after provisioning").
  - New `test_exec_refuses_an_override_that_appeared_in_an_automatic_worktree`: after `up` in the
    automatic arrangement (wtenv's `compose.override.yaml`), each of these → exit 125,
    `isolation_unavailable`, sentinel not run: a tracked `compose.override.yml` added
    (`override_appeared`, `path` that file); an untracked `docker-compose.override.yaml` added;
    `compose.override.yaml` replaced by a header-less file (`override_appeared`, `path` it);
    `compose.override.yaml` replaced by a symbolic link to a file in `decoy` that starts with
    wtenv's header (`symlink`; nothing read through the link).
  - As in v0.1.1 (the command runs): nothing changed since `up`; `compose.override.yaml` deleted;
    a worktree without `[compose]`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T129 [US1] ⚠RELAX(001:FR-056, FR-057) Apply check B in the automatic arrangement, in `src/wtenv/isolation.py` and `src/wtenv/execcmd.py`
  - Needs T052 (the four override names and the header line, as check 7 uses them). `lstat` on
    the four names and one read of the recorded file's first line; the link check comes before
    the read. `exec` still does not import `wtenv.compose`.
  - **Done when** `uv run pytest tests/integration/test_us1_exec_isolation.py tests/unit/test_lazy_imports.py -q` exits 0.
- [ ] T130 [US1] Mutation check: check B, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: FR-033, safety finding M5. Remove it; the refusal cases of
    `test_exec_refuses_an_override_that_appeared_in_an_automatic_worktree` must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T133 [US1] Mutation check: post-up commands of a repeat `up` get the isolation environment, in `src/wtenv/provision.py` (restored afterwards)
  - Guard: FR-033, safety finding M6. Make the repeat-`up` branch run the post-up commands with
    v0.1.1's `{**os.environ, **variables}`; `test_post_up_commands_get_the_isolation_environment_on_a_repeat_up`
    (T131) must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T140 [US1] Add failing tests for check 2's override-name rule to `tests/integration/test_us1_exec_isolation.py`
  - FR-033 check 2; safety finding L4; data-model.md, `exec` checks, row 2.
  - Hand-edited registry, `repository_override` set to each of: `deploy/compose.prod.yml`;
    `compose.prod.yml`; `compose.override.yml` when the compose file is `deploy/compose.yaml`;
    `deploy/compose.override.yml` when the compose file is at the root (each file present). Each
    → exit 125, `recorded_value_invalid`, sentinel not run. Accepted: `compose.override.yml`
    beside a root `compose.yaml`; `deploy/docker-compose.override.yaml` beside
    `deploy/compose.yaml`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T141 [US1] Require `repository_override` to be one of the four override names beside the compose file, in check 2 of `src/wtenv/isolation.py`
  - Needs T052. `posixpath.dirname(repository_override) == posixpath.dirname(file)` and the base
    name is one of the four names, after `recorded_path_problem`.
  - **Done when** `uv run pytest tests/integration/test_us1_exec_isolation.py -q` exits 0.
- [ ] T142 [US1] Mutation check: check 2's override-name rule, in `src/wtenv/isolation.py` (restored afterwards)
  - Guard: safety finding L4. Accept any plain path (only `recorded_path_problem`); the
    `deploy/compose.prod.yml` case of T140 must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T143 [US1] Mutation check: post-up commands get the isolation environment at all (FR-033), in `src/wtenv/provision.py` (restored afterwards)
  - Guard: safety review, item 8. Make `_run_post_up` use v0.1.1's `{**os.environ, **variables}`
    at both call sites; `test_post_up_commands_get_the_same_compose_environment_as_exec` (T051)
    and `test_post_up_commands_get_the_isolation_environment_on_a_repeat_up` (T131) must both fail.
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
  - Symbolic links (001:FR-086): `.wtenv/` a link to a directory in `decoy`, and, separately,
    `.wtenv/compose.isolation.yaml` a link to a file with wtenv's header in `decoy`.
    `down --dry-run`, `down`, `gc` and `gc --release` report the isolation file under `failed`
    and keep its record; `snapshot_tree(decoy)` is unchanged.
  - New decoy test `test_a_hand_edited_repository_override_record_is_never_deleted`:
    `repository_override` hand-edited to a developer file in the worktree, to `../outside.yml`,
    and to an absolute path; `down --dry-run`, `down`, `gc`, `gc --release` never list or touch the
    decoy.
  - **Done when** these cases fail under the failing-test rule.
- [ ] T066 [US1] ⚠RELAX(001:FR-087 outcome) Report adopted files in `kept_items`, in `src/wtenv/teardown.py`
  - `override_problem` already accepts exactly `.wtenv/compose.isolation.yaml` (T105). `_override_step`: a header-less file that `gitutil.is_tracked`
    reports is a `KeptItem`, not a `FailedItem`, and is never unlinked. `down` and `gc` look only
    at `ComposeRecord.override`, never at `repository_override`.
  - **Done when** `uv run pytest tests/integration/test_us1_repository_override.py tests/integration/test_hand_edited_registry.py tests/unit/test_teardown_paths.py -q` exits 0.
- [ ] T067 [US1] Mutation check: exact-path acceptance in `override_problem`, in `src/wtenv/compose.py` (restored afterwards)
  - Widen it to any `.wtenv/*.yaml`; the `.wtenv/notes.yaml` cases of T104 and T065 must fail.
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
- [ ] T110 [US1] Mutation check: no symbolic link when teardown deletes the isolation file (001:FR-086), in `src/wtenv/teardown.py` (restored afterwards)
  - Guard: analysis G1. Remove the `symlinked_part` check in `_override_step`; the symbolic-link
    cases of T065 (`.wtenv/` and the isolation file) must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T134 [US1] Add failing tests for the header recheck after the `removing` save to `tests/integration/test_us3_compose.py` and `tests/unit/test_teardown_paths.py`
  - FR-007, FR-009, 001:FR-087 ("immediately before"); safety finding L1; plan.md S2.
  - In `up` (the `[compose]`-removed transition of T041) and in `down` and `gc`: monkeypatch the
    `removing` save (`provision._save_compose_record`, `teardown._set_override_state`) so that,
    after saving, it replaces the file at the recorded path with an untracked header-less file,
    as a checkout during the registry-lock wait would. That file survives byte-identical; `up`
    fails with `ownership_conflict`; `down` and `gc` report it under `failed` and keep the
    record. Kept: `test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T135 [US1] Recheck the header after the `removing` save, immediately before the unlink, in `_remove_override` (`src/wtenv/provision.py`) and `_override_step` (`src/wtenv/teardown.py`)
  - Needs T066. The check before the save stays (a header-less file is never marked
    `removing`); the new one runs after the save, right before `unlink`. No new unlink call site.
  - **Done when** `uv run pytest tests/integration/test_us3_compose.py tests/unit/test_teardown_paths.py -q` exits 0.
- [ ] T136 [US1] Mutation check: the header recheck after the `removing` save, in `src/wtenv/provision.py` and `src/wtenv/teardown.py` (restored afterwards)
  - Guard: 001:FR-087, safety finding L1. Remove the check after the save, one file at a time;
    the `up` cases of T134 must fail with the first, the `down`/`gc` cases with the second.
  - **Done when** as in Working rules, "Mutation check", for both mutations.
- [ ] T137 [US1] ⚠RELAX(001:FR-087 outcome) Add failing tests for a git-tracked file that keeps wtenv's header to `tests/unit/test_override_inventory.py`, `tests/integration/test_us1_repository_override.py`, and `tests/unit/test_teardown_paths.py`
  - FR-006 (amended by safety finding L2), SC-007; data-model.md, Override inventory, row
    "recorded, header present, tracked"; cli.md, `wtenv up` step 5 and `wtenv down`; plan.md,
    Decisions to review 8.
  - After an automatic `up`, `git add -f compose.override.yaml` (wtenv's file, header intact):
    a second `up` → exit 0 and adoption as in T046 (`exec_only`, `repository_override` the
    path, warning `compose_override_adopted`), the file byte-identical, mode and mtime
    unchanged. `down --dry-run`, `down`, `gc`, `gc --release` → `kept_items`
    (`repository_owned`), never `removed`; the file byte-identical.
  - After an exec-only `up`, `git add -f .wtenv/compose.isolation.yaml`: `up` →
    `ownership_conflict` (exit 11), nothing changed; `down` and `gc` → `kept_items`, the file
    byte-identical.
  - The race of T134 with a tracked file that keeps the header: the file survives.
  - Inventory unit: recorded path, header present, tracked → `adopt`; untracked → `wtenv_own`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T138 [US1] ⚠RELAX(001:FR-087 outcome) Never rewrite or delete a git-tracked file at a recorded path, in `src/wtenv/compose.py`, `src/wtenv/provision.py`, and `src/wtenv/teardown.py`
  - Needs T135. `gitutil.is_tracked` is asked whenever a file exists at the recorded path, not
    only when the header is missing: in the inventory, in the step-5 isolation-path check, in
    the recheck before every rewrite (`_compose_step`) and unlink (`_remove_override`, before
    and after the `removing` save), and in `_override_step`.
  - **Done when** `uv run pytest tests/unit/test_override_inventory.py tests/integration/test_us1_repository_override.py tests/integration/test_us3_compose.py tests/unit/test_teardown_paths.py -q` exits 0.
- [ ] T139 [US1] Mutation check: `is_tracked` for a file that keeps wtenv's header, in `src/wtenv/compose.py` (restored afterwards)
  - Guard: FR-006, safety finding L2. Ask `is_tracked` only when the header is missing (the
    order before L2); the header-carrying tracked cases of T137 must fail.
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
- [ ] T144 [US1] Mutation check: `up` never writes, deletes or excludes the repository override (FR-002), in `src/wtenv/provision.py` (restored afterwards)
  - Guard: plan.md, requirement map, FR-002; safety review, item 8 (T049 covers only the adoption
    unlink, T070 only teardown). Three mutations, one at a time: (1) in `_compose_step`, also
    write the isolation text to the path in `repository_override`; (2) when `[compose]` is
    removed in the exec-only arrangement (T041), also unlink `repository_override`; (3) in
    `_registry_step`, also add the repository override's pattern to `exclude_patterns`.
    `test_the_repository_override_is_byte_identical_through_up_down_gc_and_dry_runs` (T072) must
    fail on (1), the `[compose]`-removed case of T041 on (2), and the exclude assertion of T032
    ("never the repository override's path") on (3).
  - **Done when** as in Working rules, "Mutation check", for each of the three mutations.
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
- [ ] T145 [US3] Mutation check: `{compose_env:NAME}` values are percent-encoded, in `src/wtenv/database.py` (restored afterwards)
  - Guard: plan.md, "Points the maintainer should confirm" 5; data-model.md, Compose-hosted
    database, `url`; safety review, item 8 (it keeps the URL's host local, FR-018, 001:FR-025).
    Insert the value verbatim; the T075 case with a password holding `@`, `/`, `:` and `%` must
    fail.
  - **Done when** as in Working rules, "Mutation check".
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
- [ ] T112 [US3] Add failing tests for volume names judged under the wtenv project to `tests/unit/test_compose_database.py`, `tests/unit/test_compose_model.py`, and `tests/integration/test_us3_storage_names.py` (new; `pytestmark = pytest.mark.integration`; Docker)
  - FR-027, SC-003, SC-004; safety finding H1; data-model.md, Data storage classification;
    research R4, R7 (last paragraph).
  - The case: the compose file sets `name: app`; service `db` mounts volume `pgdata`; top-level
    `volumes: {pgdata: {name: app_pgdata}}`, a name that starts with the repository's own
    project name; the worktree's wtenv project is the generated `wtenv-feature-x-<hash>`, which
    differs from `app`. Expected: `database_storage_shared`, `storage` `fixed_name_volume`,
    `name` `app_pgdata`. The same with no `name:` in the compose file and the compose file's
    directory named `app`. Control: `volumes: {pgdata: {}}` → project-scoped, accepted, no
    warning.
  - Unit: through the step-5 resolution with a stub runner that answers as Compose does
    (research R4): without `-p`, model `name` `app` and the unnamed `pgdata` resolved to
    `app_pgdata`; with `-p P`, model `name` P and the unnamed `pgdata` resolved to `P_pgdata`;
    a `name:` is kept as written either way. `resolution_command` includes `-p <project>` with
    `type = "compose"` and is v0.1.1's command otherwise (T029's case unchanged).
  - Integration: the same files resolved by the real Compose through `compose.resolve_model` and
    `compose.database_storage`. Only `docker compose config` runs; nothing is created.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T113 [US3] Resolve with `-p <project>` for the storage check, in `src/wtenv/compose.py` and `src/wtenv/provision.py`
  - Needs T079. `resolution_command` takes an optional project; `_compose_plan` passes the
    recorded or generated project when `[database] type = "compose"`. `database_storage`
    compares a volume's `name` with `<project>_<key>`. Without `type = "compose"`, the command
    and every result are v0.1.1's (FR-008, FR-028).
  - **Done when** `uv run pytest tests/unit/test_compose_database.py tests/unit/test_compose_model.py tests/integration/test_us3_storage_names.py -q` exits 0.
- [ ] T125 [US3] Add failing tests for a volume device that is not an absolute path to `tests/unit/test_compose_database.py`
  - FR-027 (amended by safety finding M4); data-model.md, Data storage classification, row
    `device_not_absolute`; research R4.
  - `database_storage_shared`, `storage` `device_not_absolute`, `details.name` the device as
    written, for: `driver_opts: {type: nfs, o: "addr=10.0.0.5,rw", device: ":/exports/pg"}`;
    `type: nfs` with `device: exports/pg`; `type: cifs` with `device: nas/pg`; `type: none`,
    `o: bind` with `device: ./data` and with `device: data`. The test runs with the current
    directory set to the worktree root, so a device resolved against it would land inside the
    worktree.
  - Unchanged: an absolute device is still judged as a bind (inside → warning
    `worktree_directory`, outside → `host_directory`); a volume without `driver_opts`.
  - **Done when** the new cases fail under the failing-test rule.
- [ ] T126 [US3] Refuse a non-absolute `nfs`, `cifs` or local-bind device, in `database_storage` (`src/wtenv/compose.py`)
  - Needs T113. `os.path.isabs(device)` on the device as written; a relative device is never
    passed to `realpath`.
  - **Done when** `uv run pytest tests/unit/test_compose_database.py -q` exits 0.
- [ ] T127 [US3] Mutation check: the non-absolute device row, in `src/wtenv/compose.py` (restored afterwards)
  - Guard: FR-027, safety finding M4. Judge a relative device as a bind resolved against the
    current directory (the plan before M4); the `device: ./data` case of T125 must fail (it is
    then classed `worktree_directory`).
  - **Done when** as in Working rules, "Mutation check".
- [ ] T080 [US3] Mutation check: FR-027 row "external volume", in `src/wtenv/compose.py` (restored afterwards)
  - Classify `external: true` as accepted; its case must fail.
  - **Done when** as in Working rules, "Mutation check".
- [ ] T081 [US3] Mutation check: FR-027 row "fixed-name volume" (R8 name rule, judged under the wtenv project), in `src/wtenv/compose.py` (restored afterwards)
  - Restore the old comparison: resolve without `-p` and compare the volume's `name` with
    `<model name>_<key>`. The case of T112 (compose `name: app`, volume `name: app_pgdata`, a
    wtenv project other than `app`) must fail, in the unit and in the integration test. A case
    whose fixed name has nothing in common with the project name does not count: it is refused
    under the old comparison too, so it cannot show safety finding H1.
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
  - Removes the interim guard in `provision._database_plan` (an `unsupported` error, reason
    `compose_database_not_provisioned`, added after T013 so that a `type = "compose"` configuration
    is refused cleanly until this task) and deletes its test,
    `tests/unit/test_compose_database_guard.py`.
  - **Done when** `uv run pytest tests/integration/test_us3_compose_database.py tests/integration/test_us3_compose.py -q` exits 0,
    and `grep -rn "compose_database_not_provisioned" src/ tests/` prints nothing.
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
  - New `test_every_v0_1_1_config_fixture_is_accepted_and_provisions_identically`, both halves
    of FR-028: (1) for each of the three exception fixtures of T002 (`dup_port`,
    `colliding_names`, `ports_named_like_a_variable`), `ports.check_port_variables` raises
    `port_variable_conflict`, and that is the only difference from the golden; (2) for every
    other T002 configuration, the new code's results from the same public functions and fixed
    inputs equal the golden ports, project name, database name or path, and override bytes, and
    the env section equals the golden apart from the added `WTENV_PORT_…` lines. The test fails
    if a configuration outside the three is refused, or if one of the three is accepted.
  - SC-009: every new error code (20–23 and the new `reason` values) and every new warning, when
    produced by its command with `--json` and standard input closed, is exactly one JSON
    document on stdout that validates against `json_models.py`.
  - Expected to pass if Phases 2–5 are right. Run them; a failure goes to T092.
  - **Done when** `uv run pytest tests/unit/test_v0_1_1_compatibility.py tests/contract/test_json_documents.py -q`
    has been run and its result (all passed, or the failing test IDs) is recorded under this task.
- [ ] T092 Fix what T091 finds, in the module the failure points at
  - A difference from v0.1.1 that FR-028 does not allow is a bug. Do not change the golden.
    FR-028 allows exactly one difference: the `port_variable_conflict` refusal of its three
    exception setups. Keep that refusal (T015, T029); never remove it to match a golden.
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
  - FR-002, FR-004, FR-011, FR-025, FR-033; cli.md, "User documentation (README) obligations";
    spec, Known limits.
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
    file's directory there; the three setups FR-028 refuses (one container port published twice,
    service names that map to one variable name such as `my-api` and `my_api`, a `ports` entry
    named like a `WTENV_PORT_…` variable); every writable mount
    of the database service counts as data (use `:ro`); fixed `image:` tags are shared between
    worktrees; `isolation_unavailable` reasons and fixes; exit statuses 20–23 in the status table.
  - **`.env` files and the exclude block** (safety findings M2, M3; spec, Known limits): through
    `wtenv exec` in an exec-only worktree Compose reads only the `.env` beside the compose file
    (`COMPOSE_ENV_FILES`); elsewhere a `.env` in the current directory (Compose 2.24.4: instead
    of the project's; 5.1.4: as well, winning) or a `COMPOSE_ENV_FILES` file can change the
    project name, so run Compose from the compose file's directory. An untracked override of
    your own can be ignored by git through wtenv's exclude block, written for another worktree;
    then a checkout or `git clean -X` can overwrite or delete it, and `up` warns
    `compose_override_excluded`.
  - **Downgrade** (D1): before going back to v0.1.x, run `wtenv down` in every exec-only
    worktree. Otherwise the registry, one file for every repository on the machine, becomes
    unreadable to v0.1.x (`registry_unreadable`), and the post-checkout hook then fails silently.
  - **Done when** each of these prints at least one line:
    `grep -n 'WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>' README.md`,
    `grep -n 'without isolation' README.md`, `grep -n 'type = "compose"' README.md`,
    `grep -n 'host-side' README.md`, `grep -n 'isolation_unavailable' README.md`,
    `grep -n 'port_variable_conflict' README.md`, `grep -n '2.24.4' README.md`,
    `grep -n 'my_api' README.md`, `grep -in 'downgrad' README.md`,
    `grep -n 'compose_override_excluded' README.md`, `grep -n 'COMPOSE_ENV_FILES' README.md`; and
    `grep -c 'already exists and wtenv did not' README.md` prints `0` (the v0.1 refusal line is
    gone).
- [ ] T096 [P] Update `docs/roadmap.md`
  - Principle X; plan.md, "Roadmap entries". New heading "From planning feature 002
    (2026-10-10)" with the four entries: a `--project-directory` equivalent so Compose 2.24.4 reads
    `.env` from any subdirectory; showing a compose-hosted database in `ls`; inspecting the image's
    `VOLUME`/`PGDATA` to narrow FR-026/FR-027; naming two host addresses of one container port
    apart. In "From the fastapi-template dogfood run", mark the three rows delivered in 0.2.0
    (spec 002), noting the variable names are `WTENV_PORT_…`, not the `WTENV_BACKEND_8000` example.
    Add every idea noted during implementation, with its source, and the entries proposed in
    this file's "Deferred" section (safety review, 2026-10-10).
  - **Done when** `grep -n 'From planning feature 002' docs/roadmap.md`,
    `grep -c 'spec 002' docs/roadmap.md` (≥ 3) and `grep -n 'volumes_from' docs/roadmap.md` all
    succeed.
- [ ] T097 Group check 7A: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED`. Commit `docs: document overrides, port variables and compose-hosted databases`.

### 7B. CI and release preparation (`.github/workflows/ci.yml`, `CHANGELOG.md`, `src/wtenv/__init__.py`)

- [ ] T098 Make the `minimums` job (Compose 2.24.4) run the new integration tests with nothing skipped, in `.github/workflows/ci.yml`
  - Plan.md, Test strategy and Coverage. In `minimums`: `pytest -m integration -rA` so passes are
    listed; a step that fails unless the log has `PASSED` for
    `test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose` and has
    lines for `test_us1_repository_override.py`, `test_us1_exec_isolation.py`,
    `test_us2_port_variables.py`, `test_us3_compose_database.py` and `test_us3_storage_names.py`; the existing no-skip step
    unchanged (only `--orphan` allowed). Pull every image the new tests use (expected:
    `postgres:17` and `valkey/valkey:latest`, already pulled; add any other in all three jobs).
  - `coverage` job: add `uv run coverage report --fail-under=80 --include='*/wtenv/isolation.py'`.
  - Actions stay pinned to full SHAs.
  - The push and the CI run move to T111, after T101, so CI checks the final commit (O3).
  - **Done when** `grep -n 'test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose' .github/workflows/ci.yml`
    and `grep -n "wtenv/isolation.py" .github/workflows/ci.yml` each print a line, and
    `grep -n 'uses: ' .github/workflows/ci.yml | grep -vE '@[0-9a-f]{40}'` prints nothing.
- [ ] T099 [P] Add the 0.2.0 entry to `CHANGELOG.md`
  - Keep a Changelog format, `## [0.2.0] - <date of this task>` under `[Unreleased]`. **Added**:
    repository-owned override support (exec-only arrangement, `.wtenv/compose.isolation.yaml`,
    `wtenv exec`/post-up isolation), port variables, `[database] type = "compose"`, error codes
    20–23, the three warnings, `kept_items`. **Changed**, stated as a behaviour change: the three
    setups of the FR-028 exception now fail with `port_variable_conflict` (exit 22), where v0.1.1
    accepted them: a compose setup that publishes one container port twice (for example a
    repository override that adds a second host port for the same container port); two service
    names that map to one variable name (`my-api` and `my_api`, `API` and `api`); and a `ports`
    entry named like a `WTENV_PORT_…` variable. Existing compose users see new `WTENV_PORT_…`
    lines in wtenv's env-file section, reported `updated` on the first `up`. Also changed for
    v0.1.1 setups (plan.md, Decisions to review): `up` refuses `COMPOSE_PROJECT_NAME` and
    `COMPOSE_FILE` in the `.env` forms `KEY: value` and after a byte-order mark; `exec` exits 125
    when an inherited `COMPOSE_FILE` names another worktree's isolation file, or when an override
    file appeared beside wtenv's override; a git-tracked generated override is adopted and kept,
    never rewritten or deleted. **Known limits**: a
    plain `docker compose` in an exec-only worktree is not isolated; before downgrading to
    v0.1.x, run `wtenv down` in exec-only worktrees, or the registry becomes unreadable to v0.1.x
    for every worktree (D1).
  - **Done when** `grep -n '## \[0.2.0\]' CHANGELOG.md` and
    `awk '/## \[0.2.0\]/,/## \[0.1.1\]/' CHANGELOG.md | grep -n 'port_variable_conflict'` both print a line, and
    the same `awk` piped to `grep -n 'my_api'` and to `grep -in 'downgrad'` each print a line.
- [ ] T100 [P] Bump the version to 0.2.0 in `src/wtenv/__init__.py`, without tagging
  - The single source of the version (hatch reads it). No tag, no push of a tag, no release.
  - **Done when** `uv run wtenv --version` prints `0.2.0`, `git tag --list 'v0.2*'` prints
    nothing, `uv build` succeeds, and `uv run pytest tests/integration/test_build_contents.py -q`
    exits 0.
- [ ] T101 Group check 7B: runs G5 from the repository root; edits no file
  - **Done when** G5 prints `G5 PASSED` and `grep -rn NotImplementedError src/` prints nothing.
    Commit `chore(release): prepare 0.2.0`.

- [ ] T111 Push the branch and see CI green on the final commit
  - Plan.md, Test strategy and Coverage; analysis O3. Runs after T101, so CI checks every commit
    of T095–T101. Needs the maintainer's go-ahead to push (normal push, never force).
  - **Done when** the branch is pushed and
    `gh run list --branch 002-compose-dev-setup --workflow CI --limit 1 --json conclusion,headSha -q '.[0]'`
    shows `conclusion` `success` with `headSha` equal to `git rev-parse HEAD`, and
    `gh run view <id> --log --job <minimums job id> | grep -c 'PASSED .*test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose'`
    prints at least 1.

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

- **Phase 1** (T001–T002): none. T002 must finish before T008, T012, T091, T108.
- **Phase 2** (T003–T014): after Phase 1. 2A, 2B and 2C are independent of each other; all three
  block every story.
- **Phase 3, US2** (T015–T024): after Phase 2. 3A → 3B → 3C.
- **Phase 4, US1** (T025–T074): after Phase 3 (the port-variable collision check runs over the
  combined model in 4B). 4A → 4B → 4C → {4D, 4E, 4F, 4G} → 4H → 4I. 4D, 4E, 4F and 4G each need
  4C; 4H needs 4E (adopted files) and 4C. Inside 4C: T104 → T105 → T032 → T033 → T106, then the
  mutation checks; T033 needs T105 (`override_problem` accepts the isolation path), and T106
  needs T033.
- **Safety review fixes** (T112–T150): each sits in the group whose code it changes and runs
  failing test → change → mutation check in file order. 4A: T147 after T027. 4B: T146 after
  T030. 4C: T122 → T123 → T124 after T107 (T123 needs T033). 4F: T131 → T132 between T051 and
  T052 (T132 needs T033); after T060: T114 → T115 → T116 → T148 → T149 → T150, T117 → T118 → T119–T121, T128 → T129 →
  T130, T133, T140 → T141 → T142, T143 (T115, T118, T129 and T141 need T052; T148 needs T115 and T132, T149 needs T115 and T132). 4H: T134 → T135 →
  T136 → T137 → T138 → T139 after T110 (T135 needs T066, T138 needs T135). 4I: T144 after T073.
  5A: T145 after T076. 5B: T112 → T113 → T125 → T126 → T127 after T079 (T113 needs T079, T126
  needs T113), before T080; T081 uses T112's case.
- **Phase 5, US3** (T075–T090): after Phase 4 (the database service is found in the combined
  model; teardown runs in both arrangements). 5A and 5B are independent; 5C needs both; 5D needs 5C.
- **Phase 6** (T091–T094): after Phase 5.
- **Phase 7** (T095–T103, T111): 7A after Phase 6; 7B after 7A; T111 after T101; 7C last.

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
| ⚠RELAX(001:FR-087 outcome): a git-tracked file at the recorded path, header-less or keeping wtenv's header, is adopted/kept instead of `ownership_conflict`/`failed` or a rewrite/delete | T046, T047, T065, T066, T137, T138 | S3: header check unchanged; `is_tracked` required; never written or deleted; untracked case unchanged (T046, T068, T139) |
| ⚠RELAX(001:FR-056, FR-057): in exec-only worktrees `exec` refuses every command when it cannot apply the isolation settings; in every other worktree only checks A and B (safety M1, M5) | T051, T052, T114, T115, T128, T129 | S5: only when a repository-owned override is detected, or for checks A and B; one stable code, `isolation_unavailable`; the automatic arrangement's environment unchanged (T051 `test_exec_in_an_automatic_worktree_sets_exactly_the_section`) |

### Mutation checks, one per guard

| Guard | Plan section | Task |
|-------|--------------|------|
| `exclude_if` on `repository_override` | S7 | T010 |
| Collision check | R5, FR-012 | T017 |
| Record before create (isolation file) | S1 | T034 |
| Unrecorded file at the isolation path refused | S1 | T035 |
| Header check before rewrite | S1 | T036 |
| Symbolic-link refusal of `.wtenv/` | S1 | T037 |
| Link refused at every recorded override path (after the O2 change) | S1 | T107 |
| Isolation file excluded from `git status` | S1 | T038 |
| Verification removes only the file just written | S1 | T039 |
| Header check immediately before unlink (`up`) | S2 | T043 |
| `removing` recorded before unlink | S2 | T044 |
| `is_tracked` in adoption (`up`) | S3 | T048 |
| Adoption never writes or deletes | S3 | T049 |
| FR-033 exec checks 1–7 | S5 | T053–T059 |
| FR-033 check 1 reads `.env` beside the compose file | S5 | T109 |
| `COMPOSE_PROJECT_NAME` not injected | S5 | T060 |
| Exact-path acceptance in `override_problem` | S4 | T067 |
| `is_tracked` in the teardown outcome | S3 | T068 |
| Header check before delete (teardown) | S4 | T069 |
| Only `ComposeRecord.override` is deleted | FR-002 | T070 |
| Symbolic-link refusal when teardown deletes the isolation file | S4 | T110 |
| FR-027 rows: external, fixed name (judged under the wtenv project, safety H1), device, outside bind | S6, R4 | T080–T083 |
| `<project>_` prefix rule (reading R8) | S6 | T089 |
| FR-027 row: non-absolute `nfs`/`cifs`/local-bind device (safety M4) | R4 | T127 |
| FR-033 check A: inherited `COMPOSE_FILE` naming another worktree's isolation file (safety M1) | S5 | T116 |
| FR-033 check A before post-up commands, both call sites (safety M1, decided) | S5 | T150 |
| `.env` pattern accepts `:` and a byte-order mark (safety M2) | S5, R7 | T119 |
| `exec` sets `COMPOSE_ENV_FILES`; an inherited one is refused (safety M2) | S5, R7 | T120, T121 |
| FR-033 check B: an override appeared in the automatic arrangement (safety M5) | S5 | T130 |
| Post-up isolation on a repeat `up` (safety M6) | S5 | T133 |
| Post-up isolation at all (safety review, item 8) | S5 | T143 |
| Check 2: `repository_override` is an override name beside the compose file (safety L4) | S5 | T142 |
| Excluded-override warning (safety M3) | FR-002 | T124 |
| Header recheck after the `removing` save (safety L1) | S2, S4 | T136 |
| `is_tracked` for a file that keeps wtenv's header (safety L2) | S3 | T139 |
| `up` never writes, deletes or excludes the repository override (item 8) | FR-002 | T144 |
| `{compose_env:}` percent-encoding (item 8) | FR-018 | T145 |
| `compose_path_separator` refusal (item 8) | R2 | T146 |
| Inventory: unrecorded file with wtenv's header is `ownership_conflict` (item 8, C3) | FR-001 | T147 |

## Requirement coverage

Every requirement of spec.md maps to at least one task. Checked against FR-001…FR-033 and
SC-001…SC-009: none is uncovered.

| Requirement | Tasks |
|-------------|-------|
| FR-001 | T025, T026, T027, T032, T033, T072, T106, T147 |
| FR-002 | T032, T033, T041, T065, T066, T070, T072, T106, T122, T123, T124, T144 |
| FR-003 | T029, T030, T032, T033, T146 |
| FR-004 | T051, T052, T062, T095 |
| FR-005 | T062, T063 |
| FR-006 | T026, T046, T047, T048, T049, T065, T066, T068, T137, T138, T139 |
| FR-007 | T025, T026, T027, T041, T042, T134, T135, T136 |
| FR-008 | T002, T032, T041, T091 |
| FR-009 | T032–T039, T041, T065–T069, T104–T107, T110, T134, T135, T136 |
| FR-010 | T019, T020, T131, T132 |
| FR-011 | T015, T016, T095 |
| FR-012 | T015, T016, T017, T019, T029, T085 |
| FR-013 | T019, T020, T086 |
| FR-014 | T022, T023 |
| FR-015 | T016, T019, T029 |
| FR-016 | T019 |
| FR-017 | T012, T013 |
| FR-018 | T012, T013, T075, T076, T145 |
| FR-019 | T085, T086 |
| FR-020 | T008, T012, T075, T085 |
| FR-021 | T078, T079, T085 |
| FR-022 | T085, T086 |
| FR-023 | T088, T089 |
| FR-024 | T085, T086 |
| FR-025 | T095 |
| FR-026 | T078, T079, T085 |
| FR-027 | T078–T083, T085, T112, T113, T125, T126, T127 |
| FR-028 | T002, T012, T019, T091, T092 |
| FR-029 | T002, T008, T009, T010, T108 |
| FR-030 | T010, T017, T034–T039, T043, T044, T048, T049, T053–T060, T067–T070, T080–T083, T089, T091, T107, T109, T110, T116, T119–T121, T124, T127, T130, T133, T136, T139, T142–T147, T150 |
| FR-031 | T003, T004, T005, T006 |
| FR-032 | T003, T006, T062, T091 |
| FR-033 | T051–T061, T109, T114–T121, T128–T133, T140–T143, T148–T150 |
| SC-001 | T102 |
| SC-002 | T019, T102 |
| SC-003 | T085, T102, T112 |
| SC-004 | T088, T102, T112 |
| SC-005 | T072, T102 |
| SC-006 | T091, T093, T102 |
| SC-007 | T065, T072, T088, T102, T137 |
| SC-008 | T015, T102 |
| SC-009 | T091, T102 |

## Notes

- Total: 150 tasks (T001–T150). T104–T111 were added by the analysis fixes, T112–T147 by the
  safety review fixes, and T148–T150 by the maintainer's decision on safety finding M1 (post-up
  commands); their place in the file, not their number, gives the order.
- plan.md's "Existing tests whose expected outcome this feature changes" names three tests; the
  code has five that assert the v0.1 refusal. The maintainer chose (2026-10-10) to rewrite all
  five (T025, T032), and a sixth for the symbolic-link rule (T106). plan.md is not edited by this
  task list.
- The contract tests move from 001's contract to 002's (T003); 002's `json_models.py` is the full
  schema-1 contract, so every 001 model is still checked.

## Analysis fixes

Fixes for the HIGH and MEDIUM findings of the spec 002 analysis (2026-10-10), applied to the
spec artifacts only. Line numbers are those of this commit. The maintainer decided C1 (all three
FR-012 cases are the FR-028 exception), O1 (a separate task before T033), O2 (a link at an
unrecorded override name is the repository's), U1 (extend check 1 to `.env`), O3 (push after
T101) and G2 (the mutation unlinks directly). The 14 LOW findings (G3–G6, U3, O4, T1–T4, A2, A3,
R2, D2) are not applied.

| Finding | What changed | File and lines |
|---------|--------------|----------------|
| C1 (HIGH) | FR-028 gets one exception: the three setups the FR-011 rule cannot name (one container port published twice, two service names that map to one name, a `ports` entry equal to a derived name) are refused with `port_variable_conflict`; every other v0.1.1 `wtenv.toml` works unchanged. The exception is repeated in SC-006, FR-008, US1 sc6 and US3 sc8. Story 2 now "narrows 001:FR-031" instead of "relaxes nothing", and the 001:FR-031 row is marked **Narrowed**. The plan's FR-028 row, point 3 and relaxations table cite the exception. The T099 CHANGELOG names all three setups | spec.md:117-118, 224-226, 330-331, 415-424, 453-455, 465, 520-522; plan.md:226, 370, 441-445; tasks.md:1000-1016 (T099) |
| C2 (HIGH) | T002 adds three v0.1.1 fixtures (`dup_port`, `colliding_names`, `ports_named_like_a_variable`). T091 asserts both halves: the refusal for those three, and identical results for every other configuration. T092 says the refusal is the only difference FR-028 allows and must not be removed | tasks.md:119-125 (T002), 897-917 (T091, T092) |
| O1 (HIGH) | New T104 (failing `override_problem` tests) and T105 (accept exactly `.wtenv/compose.isolation.yaml`) at the start of 4C, before T033. T033 says it needs T105; T066 no longer changes `override_problem`; T067 names T104's case. Dependency lines updated | tasks.md:418-436 (T104, T105), 481-482 (T033), 725-732 (T066, T067), 1080-1082 |
| O2 (HIGH) | Spec edge case and data-model: a link at an unrecorded override name is the repository's override (kept, never written, deleted, excluded or followed for writing); a link at a recorded path or at the isolation path fails closed and no header is read through it (FR-009 says so too). New T106 adds the failing tests first (accepted at a repository override name; refused at a recorded path in both arrangements; refused at the isolation path) and then changes `_check_override_links`. It rewrites the sixth existing test, `tests/integration/test_us1_ports_env.py::test_a_compose_override_that_is_a_link_is_refused_on_a_first_up_too`. New T107 is the mutation check for the link refusal. The Working rules now allow six rewritten tests. T026 classes the link without reading through it. Plan S1 lists the guard change | spec.md:250-254, 335; data-model.md:59-66; plan.md:275; tasks.md:61-63, 384-385 (T026), 489-526 (T106), 549-553 (T107), 1133 |
| D1 (MED) | New T108 in 2B runs v0.1.1's own `registry.load` on registries saved by the new code. The automatic-only registry must load; one with an exec-only entry must give `registry_unreadable`, and neither file may change. T095 (README) and T099 (CHANGELOG) document running `wtenv down` before a downgrade | tasks.md:206-217 (T108), 960-962, 968 (T095), 1009-1016 (T099) |
| C3 (MED) | Inventory: an unrecorded regular file at another override name that starts with wtenv's header is `ownership_conflict` (as v0.1.1), not `repository_owned`. T026 has the case | data-model.md:59-61; tasks.md:378-380 (T026) |
| U1 (MED) | FR-033 check 1 also reads `.env` beside the compose file (spec FR-033, edge case, assumption; data-model check 1; cli.md details). T051 adds the `.env` cases, T052 shares the parsing rule with `up`, and new mutation T109 covers it | spec.md:285, 347-348, 567-568; data-model.md:121; contracts/cli.md:32; tasks.md:628-629 (T051), 650-652 (T052), 657-660 (T109), 1141 |
| U2 (MED) | `isolation.py` "imports only `errors`, `identity`, `registry`; never `compose`, `provision`, `database`" (no longer "standard library only"). It holds wtenv's header line and the isolation path (T105), and the `.env` pattern (T052); `compose.py` imports them | plan.md:89, 124-127; tasks.md:44-46, 428-436 (T105), 481-485 (T033), 650-652 (T052) |
| G1 (MED) | T065 adds a symlinked `.wtenv/` and a symlinked isolation file in `down`, `down --dry-run`, `gc` and `gc --release` (`failed`, target untouched). New mutation T110 removes the teardown link check. Plan S4 names both | plan.md:315-317; tasks.md:716-719 (T065), 742-745 (T110), 1147 |
| G2 (MED) | T049's mutation makes the adoption branch unlink the adopted file directly, so T046's byte-identical assertion fails | tasks.md:615-617 (T049) |
| A1 (MED) | The data-model Transitions table is rewritten as the two-save sequence (step 8 saves the old record as `removing`; step 11 unlinks, then saves the new record with `repository_override` set or cleared in that same save). It also states the consistency rule at every save, and that adoption is one step-8 save that bypasses the path-change branch. T042 and T047 say the same | data-model.md:74-89; tasks.md:579-581 (T042), 607-610 (T047) |
| O3 (MED) | T098 keeps the `ci.yml` edit, with a local, grep-based Done-when. The push and the green-CI check move to new T111, after T101, and it requires the CI run's `headSha` to equal `HEAD` | tasks.md:986-999 (T098), 1025-1032 (T111), 1086 |
| R1 (MED) | 001:FR-056 and FR-057 are marked **Relaxed** for exec-only worktrees in the spec's table and story summary, and in plan's relaxations table. T051 and T052 carry `⚠RELAX(001:FR-056, FR-057)`, which the legend and safety index list | spec.md:450-452, 473; plan.md:369; tasks.md:28-29, 624, 645, 1121 |

Coverage table, mutation index and notes updated for T104–T111: tasks.md:1074, 1133-1147,
1151-1199, 1203-1207.

## Deferred

LOW findings of the spec 002 safety review (2026-10-10) that touch no write or delete path, and
one gap found while fixing H1. They are **not** built by this task list. The text below is
proposed for `docs/roadmap.md`; T096 adds it there. `docs/roadmap.md` itself is not edited by
these fixes.

```markdown
### From the spec 002 safety review (2026-10-10)

- **Check the resolved compose database URL for a local host** (safety L3). `{env:NAME}` is
  inserted verbatim, so a value such as `x@db.internal:5432/app?` moves the host of
  `DATABASE_URL` off the machine, and host-side migrations or test resets reach a non-local
  database. Postgres mode already checks the resolved URL again (`postgres_target`); compose
  mode should apply the same host check to the resolved URL in steps 7 and 12 (FR-018,
  001:FR-025).
- **Refuse `volumes_from` on the compose-hosted database service** (safety L5). The storage
  classifier looks only at the service's own `volumes`, so `volumes_from:
  ["container:myapp-db-1"]`, or a service whose volume has a fixed name, shares data while
  `up` only warns `no_volume` (FR-027).
- **`exec` checks that the isolation file is still current** (safety L6). A `git pull` that
  adds a published port to the repository's override leaves the isolation file without an
  `!override` entry for it, so `wtenv exec -- docker compose up -d` publishes that port
  un-remapped until the next `up`. Record the SHA-256 of the repository override in the
  exec-only record (an optional field, as FR-029 allows) and fail with a new reason,
  `repository_override_changed`. v0.1.1 has the same gap for the compose file in the automatic
  arrangement.
- **`compose_fixed_volume_name` judged under the wtenv project** (found while fixing safety H1).
  The v0.1.x warning still compares a volume's `name` with the repository's own model name, so
  `name: app_pgdata` in a repository named `app` is not warned about for services other than a
  compose-hosted database. Resolving with `-p <project>` in every mode would fix it but changes
  v0.1.1's resolution command (plan.md, Decisions to review 7).
```

## Safety review fixes

Fixes for the spec 002 safety review (2026-10-10), applied to the spec artifacts only. The three
Compose claims the review took from compose-go were first checked by experiment on Compose
2.24.4 and 5.1.4 (research R7): all three hold, and two results go further (the current
directory's `.env` is read by 5.1.4 too; a `COMPOSE_ENV_FILES` file can set the project name).
The maintainer decided M1 and M5. Every fix follows the same order: failing test, change,
mutation check. Line numbers are those of this commit.

| Finding | What changed | File and lines |
|---------|--------------|----------------|
| H1 (HIGH) | The storage classifier judges volume names on the model resolved with `-p <project>` (recorded or generated wtenv project); `-p` is passed only with `[database] type = "compose"`, so v0.1.1 resolution is unchanged. New T112 (failing test: compose `name: app`, volume `name: app_pgdata`, a wtenv project other than `app`; unit with a stub runner and integration with the real Compose), T113 (change). T081 is the mutation: it restores the old comparison and must fail on T112's case | spec.md:595-597; data-model.md:185-190, 196-200; research.md:329-332, 628-634; contracts/cli.md:69-71; plan.md:226, 518-522; tasks.md:1041-1064 (T112, T113), 1089-1095 (T081) |
| M1 (MED, decided) | In any worktree that is not exec-only, `exec` fails with `isolation_unavailable` (exit 125, `compose_env_override`, `path` the other file) when an inherited `COMPOSE_FILE` names another worktree's `.wtenv/compose.isolation.yaml`; any other `COMPOSE_FILE` is v0.1.1's (check A). FR-033's "behaves as in v0.1.1" sentence and the Relationship-to-001 bullet and table row are amended. Post-up commands: not covered at first, then covered by the maintainer's decision (plan.md, Decisions to review 4), so `up` also fails with `isolation_unavailable` (exit 20) before its first post-up command, at both call sites; FR-033, the Relationship-to-001 section, data-model.md row A, cli.md and plan.md S5 amended. New T114, T115, T116, and T148, T149, T150 | spec.md:284-288, 359-367, 470-473, 494; data-model.md:131-141; contracts/cli.md:15, 32, 189-194; plan.md:208, 242, 327-331, 381, 504-507; tasks.md:733-755 |
| M2 (MED) | Check 1 and `up`'s `check_environment` share a pattern that accepts `=` or `:` and a leading byte-order mark (E6.1); check 1 also refuses an inherited `COMPOSE_ENV_FILES` (E6.3d); `exec`, post-up commands and `up`'s verification set `COMPOSE_ENV_FILES` to the checked `.env` or `/dev/null` (E6.3b, E6.3c). The spec's new Known limits state what stays undetected (automatic arrangement and plain commands). R2's E2.11 claim and known-limit text are corrected. New T117–T121 | spec.md:278, 285-288, 354-360, 589-590, 606-620; data-model.md:115, 123; research.md:14-15, 165-167, 170-182, 526-634; contracts/cli.md:15, 66-68, 100-102, 178-186, 215-223; contracts/files.md:103; plan.md:335-338, 492-503; tasks.md:756-795, 1231-1238 (T095), 1248, 1292-1297 (T099) |
| M3 (MED) | The smallest fix: in the exec-only `up`, an untracked repository override is checked with `git check-ignore -v --no-index`; a match from a line inside wtenv's block gives the new warning `compose_override_excluded` (`file`, `pattern`), and the README says why. The change for v0.1.1 users is stated in plan.md, Decisions to review 1. New T122, T123, T124 | spec.md:303-306; data-model.md:217; contracts/cli.md:60-64, 124, 223-225; contracts/files.md:61-65; contracts/json_models.py:129-133; plan.md:200, 261, 479-491; tasks.md:563-587, 1231-1238 (T095) |
| M4 (MED) | A volume of the `nfs`, `cifs` or local bind driver whose `driver_opts.device` is not an absolute path is refused with `database_storage_shared`, new `storage` value `device_not_absolute`, the device never resolved against any directory. New T125, T126, T127 | spec.md:425-427; data-model.md:199; research.md:330; contracts/cli.md:35; plan.md:226, 245, 515-517; tasks.md:1065-1085 |
| M5 (MED, decided) | In a worktree whose recorded compose arrangement is automatic, `exec` fails (`override_appeared`, or `symlink`) when another override name exists beside the compose file or the recorded override is a link or lost wtenv's header (check B; Compose prefers `compose.override.yml` and only warns, E6.2). FR-033 amended. New T128, T129, T130 | spec.md:363-367, 470-473, 494; data-model.md:135-141; research.md:581-584; contracts/cli.md:32, 189-194; plan.md:208, 327-331, 508-514; tasks.md:796-817 |
| M6 (MED) | New T131 (failing test: post-up commands of a repeat `up` with nothing changed get `COMPOSE_FILE`, `COMPOSE_PATH_SEPARATOR` and the port variables), T132 (`_run_post_up` builds its environment from the entry at both call sites), placed before T052; T133 is the mutation (repeat-`up` branch gets v0.1.1's environment) | contracts/cli.md:100-102; plan.md:208, 343-345; tasks.md:679-695, 818-822 |
| L1 (LOW, write/delete path) | The header is rechecked after the `removing` save, immediately before the unlink, in `_remove_override` and `_override_step`. New T134, T135, T136 | plan.md:283-286; tasks.md:909-925 |
| L2 (LOW, write/delete path) | A git-tracked file at a recorded path is never rewritten or deleted, whatever its header: adopted at an auto-loaded name, `ownership_conflict` at the isolation path, `kept_items` in `down`/`gc`. FR-006, the 001:FR-087 row, the inventory, the adoption row, cli.md and `KeptItem` amended. New T137, T138, T139 | spec.md:325-330, 496; data-model.md:56-58, 87; contracts/cli.md:51-53, 60-61, 139-140; contracts/json_models.py:123-125, 308-309; plan.md:204, 297-299, 523-528; tasks.md:926-950, 1415 |
| L4 (LOW, write path) | Check 2 also requires `repository_override` to be exactly `dirname(file)/` plus one of the four override names. New T140, T141, T142 | data-model.md:124; tasks.md:823-839 |
| L3, L5, L6 (LOW) | Deferred: proposed `docs/roadmap.md` text in "Deferred" (with the `compose_fixed_volume_name` gap found while fixing H1); T096 copies it to the roadmap. Not built | tasks.md:1258-1262 (T096), 1550-1582 |
| Item 8 (uncovered guards) | Mutation checks: T143 post-up isolation at both call sites; T144 `up` never writes, deletes or excludes the repository override (FR-002, three mutations); T145 `{compose_env:}` percent-encoding; T146 the `:` path refusal; T147 the inventory's refusal of an unrecorded file with wtenv's header | tasks.md:391-395 (T147), 418-421 (T146), 840-844 (T143), 971-980 (T144), 1011-1016 (T145) |

Also updated for T112–T150: the dependency lines (tasks.md:1370-1377), the relaxations and
mutation indexes (tasks.md:1415-1416, 1443-1459), the coverage table (tasks.md:1468-1507), the
notes (tasks.md:1513-1514), T098's list of CI test files (tasks.md:1273), plan.md's mutation
list (plan.md:411-417) and its new "Decisions to review" (plan.md:474-528). Research harness:
`research/e6.sh`, `research/e6_pattern.py`; logs `research/out-e6-2.24.4.txt`,
`research/out-e6-5.1.4.txt`.
