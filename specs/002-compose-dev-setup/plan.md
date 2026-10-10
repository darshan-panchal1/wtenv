# Implementation Plan: Compose dev setups — repository-owned overrides, port variables, and compose-hosted databases

**Branch**: `002-compose-dev-setup` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-compose-dev-setup/spec.md` (FR-001 to FR-033,
clarified 2026-10-10). Constitution v1.0.2.

**Artifacts**: [research.md](research.md) · [data-model.md](data-model.md) ·
[contracts/cli.md](contracts/cli.md) · [contracts/files.md](contracts/files.md) ·
[contracts/config.md](contracts/config.md) · [contracts/json_models.py](contracts/json_models.py) ·
[quickstart.md](quickstart.md)

## Summary

wtenv v0.1.x cannot provision a repository that tracks its own Compose override
(`ownership_conflict`), does not expose remapped ports by name, and has no way to describe a
database that runs as a Compose service. This plan closes the three gaps with the existing
Python code and no new dependency:

1. **Repository-owned override (P1).** When an override file that wtenv did not create and has
   not recorded sits beside the compose file, `up` stops generating the auto-loaded override
   and instead writes a recorded **isolation file**, `.wtenv/compose.isolation.yaml`, in the
   v0.1.x override layout (`name:` + `ports: !override`), computed from the **combined** model
   of the compose file and the repository's override. `wtenv exec` and post-up commands load
   the three files through `COMPOSE_FILE` (absolute paths) and fail closed when they cannot.
   Research R1 confirmed on Compose 2.24.4 that a plain `ports:` list is appended and that
   `!override` in the third file replaces the merged list. R2 confirmed that `COMPOSE_FILE`
   reaches child processes in any directory.
2. **Port variables (P2).** Every published port gets the variable
   `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>` in wtenv's env-file section and in
   `exec`. Collisions are refused before anything changes (R5).
3. **Compose-hosted database (P3).** A third `[database]` type, `compose`, names the service.
   `up` builds `DATABASE_URL` from a pattern with `{port}` (the service's remapped host port)
   and `{compose_env:NAME}` (a value from the service's resolved environment). It refuses
   shared data storage (fixed-name, external, host directory outside the worktree) and warns
   when the data lands where `down` keeps it (R3, R4). Teardown is the unchanged compose
   teardown.

For a repository with no override of its own, the generated override file, its name and its
bytes are exactly v0.1.1's (FR-008). The only visible change is the new variables in wtenv's
env-file section (FR-028). No registry migration is needed: one optional field, written only
for worktrees that use it (R6).

## Technical Context

**Language/Version**: Python 3.12 for development, 3.11 minimum (unchanged).

**Primary Dependencies**: typer, pydantic 2.13 (pinned `>=2.13,<3`; `Field(exclude_if=...)` is
used), filelock, psycopg 3, platformdirs. **No new runtime dependency.** Docker Compose is
driven as a subprocess as in v0.1.x.

**Storage**: the registry JSON file (version 1, unchanged), with one optional field added
(data-model.md). Files written in the worktree: wtenv's env-file section, and either the
v0.1.x generated override or the new isolation file.

**Testing**: pytest (`-m "not integration"` and `-m integration`), real `git worktree add` in
temporary directories, Docker tests auto-skip without Docker. The CI `minimums` job runs the
whole suite on git 2.31.0 and Compose 2.24.4.

**Target Platform**: macOS and Linux; Windows only through WSL2 (unchanged).

**Project Type**: single-package CLI (`src/wtenv/`).

**Performance Goals**: CLI startup under 300 ms (NFR-001). `exec` gains only `lstat`/`open`
calls on a few paths beside the compose file and under `.wtenv/` (the FR-033 checks, including
checks A and B of data-model.md in automatic worktrees) and does not import `wtenv.compose`.

**Constraints**: minimum versions stay git 2.31 and Compose 2.24.4 (R1 shows `!override` works
there). No registry migration (FR-029; R6). Every 001 guard kept (FR-030).

**Scale/Scope**: 2 to 5 worktrees per repository; the acceptance repository publishes 8 host
ports (+ `PORT`), inside the default block of 10.

No item of the Technical Context is left as NEEDS CLARIFICATION: research.md settles R1 to R6.

## Constitution Check

*GATE: checked before Phase 0 and again after Phase 1. Both passes below. No violation needs
the Complexity Tracking table.*

| # | Principle | Before research | After design | How this plan complies |
|---|-----------|-----------------|--------------|------------------------|
| I | Local-First | PASS | PASS | Only the local Docker engine is contacted, through `docker compose config` (as in v0.1.x) and the endpoint check `check_docker` (unchanged). A compose-hosted database is never contacted (FR-022). No telemetry. |
| II | Never Destroy User Data | PASS, with care needed | PASS | The only file wtenv may now delete that v0.1.x did not is the isolation file, which wtenv records **before** creating it and deletes only while it starts with wtenv's header (FR-009). The repository's override is never in a write, rename, delete or exclude path (FR-002; the registry field that names it is read by `exec` only). An unrecorded file at the isolation path is `ownership_conflict`. Adopting a tracked file (FR-006) only drops a record. Database data is removed only by the unchanged compose teardown, and `up` refuses data storage that other worktrees could share (FR-027). Dry runs and reporting are unchanged, plus `kept_items` (FR-006). Safety section below. |
| III | Deterministic & Idempotent | PASS | PASS | The variable rule is a pure function (R5). Env-file order is fixed (FR-013). The isolation file has the byte-fixed v0.1.x layout. Mode changes go through the existing `creating`/`removing` states, so an interrupted `up` is finished by the next one. Registry writes stay locked and atomic. |
| IV | Agent-Native | PASS | PASS | Every new fact is in `--json` (FR-032). Four new stable codes with new exit statuses 20 to 23, three new warning codes, new `reason` values; no existing code changes meaning (FR-031). No prompt. |
| V | Zero-Config Default | PASS | PASS | Stories 1 and 2 add no setting and apply whenever `[compose]` is configured (spec, Assumptions). Story 3 is opt-in through `[database] type = "compose"` in a committed `wtenv.toml`. Port and env isolation without a config file are unchanged. |
| VI | Platforms | PASS | PASS | `COMPOSE_FILE`/`COMPOSE_PATH_SEPARATOR=:` behave the same on macOS, Linux and WSL2. Paths containing `:` are refused with a stable reason (R2). |
| VII | Test-First | PASS | PASS | Test strategy below: a failing test first in every task group, a mutation check per guard, unit tests for the rule and the classifiers, integration tests with real worktrees and real Compose 2.24.4 in CI. `isolation.py` joins the 80 % coverage list. `mypy --strict` unchanged. |
| VIII | Simplicity | PASS | PASS | No new dependency, no daemon. `exec` imports one new module, `isolation.py`, which imports only `errors`, `identity` and `registry` and never `compose`, `provision` or `database` (NFR-001; the lazy-import test is extended). |
| IX | Readability | PASS | PASS | Plain functions and frozen dataclasses: an override inventory, a storage classifier, an environment builder. No metaprogramming. `exclude_if` is a documented pydantic field option with a one-line lambda. |
| X | Small Surface | PASS | PASS | New settings are only those FR-017/FR-018 require (`type = "compose"`, `service`, the `{port}` placeholder) and the credential source the spec asked planning to confirm (`{compose_env:NAME}`; spec, Assumptions). New error codes are required by FR-031. Everything else that came up is listed under "Roadmap entries" to be recorded in `docs/roadmap.md`. |

## Project Structure

### Documentation (this feature)

```text
specs/002-compose-dev-setup/
├── spec.md              # clarified 2026-10-10
├── plan.md              # this file
├── research.md          # R1–R6, with commands and output
├── research/            # experiment harness and full logs (Compose 2.24.4 and 5.1.4)
├── data-model.md
├── quickstart.md        # SC-001 to SC-004 acceptance run on the fastapi template
├── contracts/
│   ├── cli.md           # deltas to 001 contracts/cli.md
│   ├── files.md         # deltas to 001 contracts/files.md
│   ├── config.md        # deltas to 001 contracts/config.md (Story 3 settings)
│   └── json_models.py   # the full schema-1 contract with 002 additions marked "002:"
├── checklists/
└── tasks.md             # /speckit-tasks (not created here)
```

`specs/001-worktree-runtime-isolation/` is not edited. The 002 contracts state only what changes;
everything they do not mention stays as 001 defines it.

### Source Code (repository root)

```text
src/wtenv/
├── config.py       # [database] type "compose", `service`, URL placeholders {port}, {compose_env:NAME}
├── compose.py      # resolution with the repository override; override inventory; database port
│                   # and storage classification; isolation-file path in `override_problem`
├── isolation.py    # NEW; imports only errors, identity, registry (never compose, provision,
│                   # database): wtenv's header line, the isolation path, the FR-033 checks and
│                   # the COMPOSE_FILE environment, shared by exec, post-up commands and `up`'s
│                   # verification; compose.py imports the header and the path from here
├── ports.py        # port_variable_name(); collision check
├── provision.py    # `up`: mode choice, transitions, Story 2 variables, compose database URL
├── execcmd.py      # FR-033 checks before exec, COMPOSE_FILE in repository-override worktrees
├── teardown.py     # FR-006 "kept" outcome; isolation file goes through the existing override step
├── registry.py     # ComposeRecord.repository_override (optional, omitted when unset)
├── database.py     # resolve_url: {port}, {compose_env:NAME}
├── listing.py      # PortView.port_variable, WorktreeView.compose_isolation
├── output.py       # new models, codes and text lines
└── errors.py       # four new error codes, exit statuses 20–23

tests/
├── unit/           # rule, classifiers, environment builder, config, registry round trip
├── integration/    # real worktrees; Compose stacks named wtenv-test-* only
└── contract/       # models and exit statuses against specs/002-…/contracts/json_models.py
```

**Structure Decision**: the existing single-package layout. One new module, `isolation.py`,
because `exec` must apply the FR-033 checks without importing `compose.py` (NFR-001), and
because `up`'s verification and the post-up commands must use **the same** environment
builder as `exec`, so that what `up` verifies is what `exec` runs.

## Design overview

### Two compose arrangements per worktree

| | **automatic** (no repository-owned override) | **exec only** (repository-owned override present) |
|---|---|---|
| wtenv's file | auto-loaded override beside the compose file, v0.1.1 bytes (FR-008) | `.wtenv/compose.isolation.yaml`, same layout, combined model (FR-003, FR-009) |
| Recorded in | `ComposeRecord.override` | `ComposeRecord.override` (= the isolation path) + `ComposeRecord.repository_override` |
| Plain `docker compose` | isolated (001:FR-030) | **not isolated** (FR-004), and `up` warns |
| `wtenv exec`, post-up | as v0.1.1 | `COMPOSE_FILE=<compose file>:<repository override>:<isolation file>` (absolute) and `COMPOSE_PATH_SEPARATOR=:`, after the FR-033 checks |
| Verification in `up` | plain `docker compose --profile "*" config` (v0.1.1) | the same command with the `exec` environment |

The arrangement is chosen at every `up` from the files on disk (spec: "Automatic, no new setting
for Story 1"). Moving between them uses the existing path-change branch of the compose step:
the old file is recorded `removing` and removed under the header check, the new one is
recorded `creating` and written. The transitions are listed in data-model.md.

### Changes to the order of `up` (001 contracts/cli.md, `wtenv up`)

Steps 1 to 4 and 9 are unchanged. Steps 5 to 7 still change nothing, so every new refusal
leaves the worktree as it was.

- **Step 5 (compose checks)** gains, in this order: the override inventory and arrangement
  (FR-001, FR-006, FR-007; `override_ambiguous`); the isolation-path checks in the exec-only
  arrangement (symbolic links, an unrecorded file there, the header of a recorded one, `:` in a
  path); resolution of the compose file **plus** the repository's override; the port-variable
  names and their collisions (FR-012; `port_variable_conflict`); for `type = "compose"`, the
  database service's port (FR-021; `config_invalid`) and its storage (FR-026, FR-027;
  `database_storage_shared`).
- **Step 7 (database checks)**: for `type = "compose"`, the URL pattern is resolved with a
  stand-in port, so an unset `{env:NAME}` or a missing `{compose_env:NAME}` fails here. The
  real URL is built in step 12, once the block is known.
- **Step 8 (registry)** records the arrangement, before any file is written.
- **Step 10 (database)**: nothing for `type = "compose"` (FR-022).
- **Step 11 (compose file)** writes and verifies wtenv's file for the arrangement; removes
  wtenv's previous file when the arrangement changed (header rechecked immediately before).
- **Step 12 (env file)**: `ports` variables, then the port variables in rule order, then
  `DATABASE_URL` (FR-010, FR-013, FR-019).
- **Step 13 (post-up)**: the isolation environment is added in the exec-only arrangement
  (FR-033).

## Requirement map

Every new or changed requirement, the module that implements it, and the guard it must keep.
Test names marked **(new)** are the names the tasks will create; every other name is an
existing test in `tests/`.

| FR | Module(s) | What changes | Guard it must keep (and the test that holds it) |
|----|-----------|--------------|--------------------------------------------------|
| FR-001 | `compose.py` (`override_inventory`), `provision.py` (`_compose_plan`) | A repository-owned override selects the exec-only arrangement instead of `ownership_conflict` | 001:FR-087: a header-less file **at the recorded path** that git does not track is still `ownership_conflict` (`test_up_refuses_a_recorded_override_that_is_not_wtenvs_before_changing_anything`, kept) |
| FR-002 | `provision.py`, `teardown.py`, `exclude.py` (unchanged), `gitutil.py` | `repository_override` is never passed to a write, unlink, chmod or exclude call; an untracked repository override that git ignores through wtenv's block gets the warning `compose_override_excluded` (safety M3) | Only `ComposeRecord.override` is ever written or deleted (`test_the_repository_override_is_byte_identical_through_up_down_gc_and_dry_runs` **(new)**; decoy `test_a_hand_edited_repository_override_record_is_never_deleted` **(new)**) |
| FR-003 | `compose.py` (`resolution_command`, `override_text` unchanged) | Resolution reads `-f compose -f repository override`; the isolation file holds `ports: !override` for every service of the combined model | 001:FR-033: verification of exact ports and name, removal of the just-written file on failure (`test_an_override_that_fails_verification_is_removed_and_the_error_is_raised`, extended to the isolation file); `compose_port_range` for a range in the repository override |
| FR-004 | `isolation.py`, `execcmd.py`, `provision.py` (`_run_post_up`), README | `COMPOSE_FILE` + `COMPOSE_PATH_SEPARATOR` in exec and post-up; documented relaxation | exec fails closed (FR-033 tests); automatic arrangement unchanged (`test_exec_in_an_automatic_worktree_sets_exactly_the_section` **(new)**) |
| FR-005 | `output.py`, `listing.py`, `provision.py` | `WorktreeView.compose_isolation`, `compose_repository_override`; warning `compose_isolation_exec_only`; text line | FR-058 single JSON document (contract tests) |
| FR-006 | `compose.py` (inventory), `provision.py` (adoption), `teardown.py` (`_override_step`) | A **git-tracked** file at the recorded path, header-less or with wtenv's header (safety L2), is adopted: `up` drops wtenv's record of it; `down`/`gc` report it in `kept_items` and drop the record. At the isolation path, `up` refuses it (`ownership_conflict`) | Never written or deleted; `is_tracked` decides, the header no longer does for a tracked file; an untracked header-less file is still `ownership_conflict` (`test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict` **(new)**) |
| FR-007 | `compose.py` (inventory), `provision.py` (`_remove_override`) | One repository-owned + wtenv's recorded → remove wtenv's under the header check and continue; two or more repository-owned → `override_ambiguous` | 001:FR-087 header recheck immediately before unlink; `removing` recorded first; 001:FR-086 (`test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan`, kept) |
| FR-008 | `provision.py`, `compose.override_text` (unchanged) | The automatic arrangement keeps v0.1.1 behaviour | Golden file byte-identity (`test_the_example_input_generates_the_golden_file_byte_for_byte`, kept) |
| FR-009 | `provision.py` (`_registry_step`, `_compose_step`), `compose.override_problem` | The isolation file goes through every path the generated override goes through | 001:FR-067 record first, FR-018 exclude, FR-086 links, FR-087 header, recorded form: `override_problem` accepts **exactly** `.wtenv/compose.isolation.yaml` beyond the four names (`test_a_recorded_path_of_a_form_wtenv_never_records_is_left_alone`, extended with the paths listed in Safety S4) |
| FR-033 | `isolation.py`, `execcmd.py`, `provision.py` | Checks before exec; same environment for post-up at both call sites (safety M6); `COMPOSE_ENV_FILES` (M2); the widened `.env` pattern (M2); check 2 requires an override name beside the compose file (L4); checks A and B in every other worktree (M1, M5), check A also before post-up commands (M1, decided); `isolation_unavailable` | Command never run on failure (`test_exec_does_not_run_the_command_when_isolation_cannot_apply` **(new)**, one case per reason) |
| FR-010 | `ports.py`, `provision.py` (`_variables`) | One `WTENV_PORT_…` variable per published port in the section and in exec | Section markers and format (001:FR-016; the `envfile` tests, unchanged) |
| FR-011 | `ports.py` (`port_variable_name`) | The rule of R5 | Pure function: same name for every block and order (`test_the_variable_name_depends_only_on_service_port_and_protocol` **(new)**) |
| FR-012 | `ports.py` (`check_port_variables`), `provision.py` | `port_variable_conflict` before step 8 | Nothing changed on failure (`test_a_name_collision_changes_nothing` **(new)**) |
| FR-013 | `provision.py` (`_variables`) | Order: `ports`, port variables by (service, container port, protocol), `DATABASE_URL` | 001:FR-017 byte-identical repeat (`test_a_repeat_up_leaves_the_project_name_and_the_override_byte_identical`, extended to the env file) |
| FR-014 | `listing.py`, `output.py` | `PortView.port_variable`; `up`/`ls` text | — |
| FR-015 | `ports.py` (unchanged) | Variables name already-assigned ports | 001:FR-032 block size (`test_check_block_size_counts_the_published_ports`) |
| FR-016 | `provision.py` | No port variables without `[compose]` | v0.1.1 env file (`test_without_compose_the_section_is_as_in_v0_1_1` **(new)**) |
| FR-017 | `config.py` | `type = "compose"`, `service`; one mode; mode-specific keys | 001:FR-063/FR-064 closed list (`tests/unit/test_config*.py`, kept; new cases for `compose`) |
| FR-018 | `config.py`, `database.py` (`resolve_url`) | `{port}`, `{compose_env:NAME}` | 001:FR-025 local host (`test_a_compose_url_must_name_a_local_host` **(new)**) |
| FR-019 | `provision.py` | `DATABASE_URL` in the section and exec | 001:FR-019 never printed (`test_a_rejected_pattern_does_not_reveal_a_password`, kept; `test_no_up_output_contains_the_compose_database_password` **(new)**) |
| FR-020 | `database.py`; `registry.py` gains no credential field | Credentials from `{env:}` or `{compose_env:}`, in memory only | No registry model has a credential field (`test_a_password_cannot_be_stored_in_a_database_record`, kept) |
| FR-021 | `compose.py` (`database_port`) | Service exists and publishes exactly one port | `config_invalid` before step 8 (`test_a_database_service_with_two_ports_changes_nothing` **(new)**) |
| FR-022 | `provision.py` (`_database_step`) | No server contact, template copy or container start | 001:FR-034 (`test_up_starts_no_container_and_creates_no_network_or_volume`, extended) |
| FR-023 | none (by design) | Teardown is the unchanged compose teardown | 001:FR-039 label, `<project>_` prefix, unlabelled kept (`test_a_labelled_volume_with_a_fixed_name_is_kept_and_the_projects_own_is_removed`, kept) |
| FR-024 | `provision.py` (`_has_nothing_to_change`) | Repeat `up` keeps port and URL; no data reset | 001:FR-023 |
| FR-025 | README | Host-side values documented | — |
| FR-026 | `compose.py` (`database_storage`) | Warning `compose_database_data_kept` | 001 reading R6 (anonymous volumes kept) |
| FR-027 | `compose.py` (`database_storage`, `resolution_command`), `errors.py` | `database_storage_shared` before step 8; volume names judged on the model resolved with `-p <project>` (safety H1); a non-absolute `nfs`/`cifs`/local-bind device refused (M4) | Reading R8 name rule; real paths (`test_a_bind_through_a_symlink_to_outside_the_worktree_is_refused` **(new)**) |
| FR-028 | `config.py`, `provision.py` | v0.1.1 configurations unchanged, except the three setups FR-028 refuses | `test_every_v0_1_1_config_fixture_is_accepted_and_provisions_identically` **(new)** |
| FR-029 | `registry.py` | One optional field, omitted when unset | v0.1.1 registry round trip byte-identical (`test_a_v0_1_1_registry_is_saved_byte_for_byte` **(new)**); 001:FR-070 |
| FR-030 | all | No 001 guard relaxed beyond FR-004/FR-006 | Every existing guard test kept (Test strategy, "Existing tests") |
| FR-031 | `errors.py`, `output.py`, contracts | Codes 20 to 23, warnings, reasons | Contract tests compare with `contracts/json_models.py` |
| FR-032 | `output.py` | Every new fact in `--json` | Contract tests validate real output |

## Error codes, exit statuses, warnings and reasons

No existing code, status or reason changes meaning. The table of 001 keeps codes 1 to 19 and
125 to 127. The spec names four failure conditions that need "a stable error code". Each gets a
**new** code with a **new** exit status. Two incidental limits use existing codes with new
`reason` values, which FR-031 allows.

| Exit | Code (new) | Raised by | When | `details` |
|------|------------|-----------|------|-----------|
| 20 | `isolation_unavailable` | `exec` (reported with exit **125**, as every wtenv failure in `exec`); `up`, check A only, before its first post-up command (exit **20**) | FR-033: the isolation settings cannot be applied in an exec-only worktree; in any other worktree, checks A and B of data-model.md | `reason`: `compose_env_override` (+`variable`), `isolation_file_missing`, `isolation_file_not_wtenvs`, `symlink`, `recorded_value_invalid`, `repository_override_missing`, `override_appeared`; `path` |
| 21 | `override_ambiguous` | `up` | FR-007: two or more repository-owned override files beside the compose file | `files` (list of paths) |
| 22 | `port_variable_conflict` | `up` | FR-012: two published ports with one name, or a name equal to a configured variable | `variable`; `sources` (list of two descriptions) |
| 23 | `database_storage_shared` | `up` | FR-027: the database service's data is in a fixed-name volume, an external volume, a volume whose `nfs`/`cifs`/local-bind device is not an absolute path, or a host directory outside the worktree | `service`; `storage`: `fixed_name_volume`, `external_volume`, `device_not_absolute`, `host_directory`; `name` (volume name, device as written, or real path) |

Status 20 is assigned so that the 001 rule "one code, one exit status" keeps holding. Only
`exec` raises the code, and `exec` reports every failure of wtenv itself as 125 with the code in
`error.code` (001 contracts/cli.md, `wtenv exec`). That convention is unchanged.

| Existing code | New `reason` | When |
|---------------|--------------|------|
| `unsupported` (19) | `compose_path_separator` | Exec-only arrangement, and an absolute path of the compose file, repository override or isolation file contains `:` (R2) |
| `config_invalid` (3) | — (new `setting` values `database.service`, `database.type`) | FR-017, FR-021, `type = "compose"` without `[compose]` |

| Warning (new) | When | `details` |
|---------------|------|-----------|
| `compose_isolation_exec_only` | Every `up` in the exec-only arrangement (FR-004, FR-005) | `repository_override`, `isolation_file` |
| `compose_database_data_kept` | FR-026 / FR-027 accepted storage | `service`; `storage`: `anonymous_volume`, `no_volume`, `worktree_directory`; `name` when there is one |
| `compose_override_adopted` | FR-006 adoption by `up` | `file` |
| `compose_override_excluded` | FR-002: in the exec-only arrangement, `git check-ignore -v --no-index` matches the untracked repository override to a line of wtenv's exclude block (safety M3) | `file`, `pattern` |

## Safety

For every write or delete path that changes, the existing guards, how each is kept, and the
test that fails if it is removed. The mutation check in the Test strategy removes each guard
in turn and requires that named test to fail.

### S1. Writing the isolation file (`up` step 11, new path)

| Guard | Kept how | Test that breaks without it |
|-------|----------|-----------------------------|
| Recorded before created (001:FR-067, Principle II) | `_registry_step` saves `override = .wtenv/compose.isolation.yaml`, `override_state = creating`, before step 11 writes it | `test_up_interrupted_at_any_point_leaves_only_recorded_resources_and_recovers`, extended with an exec-only fixture (stop after every step) |
| An unrecorded file at the path is not wtenv's | Step 5: a file at the isolation path that the entry does not record → `ownership_conflict` (`kind` `compose_override`) | new `test_an_unrecorded_file_at_the_isolation_path_is_an_ownership_conflict_and_is_kept` |
| Header check before a rewrite (001:FR-087) | `check_recorded_override` in step 5 and again immediately before the write in step 11, on the isolation path | `test_up_does_not_rewrite_an_override_that_lost_the_header_after_the_plan`, parametrised over both arrangements |
| No symbolic link (001:FR-086) | `_refuse_symlink(root, ".wtenv/compose.isolation.yaml")` covers `.wtenv/` and the file | new `test_a_symlinked_wtenv_directory_is_refused_in_the_exec_only_arrangement` |
| Links at wtenv's paths only (001:FR-086, FR-009) | **Guard change** in `_check_override_links`: a link at an unrecorded override name is the repository's override and is no longer refused; a link at the recorded path or at the isolation path still fails closed, with no header read through it | `test_a_compose_override_that_is_a_link_is_refused_before_anything_changes` (kept) and the isolation-path link case (tasks T106, T107); `test_a_compose_override_that_is_a_link_is_refused_on_a_first_up_too` is rewritten (T106) |
| Excluded from `git status` (001:FR-018) | `/.wtenv/compose.isolation.yaml` added to `exclude_patterns` in step 8, written in step 9 | new `test_git_status_is_clean_after_up_in_the_exec_only_arrangement` |
| Verification, and removal of only the file just written (001:FR-033) | `write_and_verify_override` unchanged; the verification command gets the `isolation.compose_environment` result | `test_a_replaced_override_that_fails_verification_is_removed_too`, parametrised |

### S2. Removing wtenv's own auto-loaded override on a change of arrangement (FR-007, FR-008)

Unchanged code path `_remove_override`: recorded form check, symbolic-link check, `removing`
recorded first, header recheck **immediately** before `unlink`, that is **after** the
`removing` save, which can wait up to 10 s for the registry lock (safety L1; v0.1.1 checks the
header before that save). The same order in teardown's `_override_step`. The only new caller is the
arrangement change, through the same branch a moved compose file uses. Tests:
`test_up_does_not_delete_an_override_that_lost_the_header_after_the_plan` (kept),
`test_a_new_compose_file_moves_the_override_and_keeps_the_project_name` (kept), new
`test_a_repository_override_appearing_removes_only_wtenvs_own_override`.

### S3. Adoption of a tracked file at the recorded path (FR-006; relaxes the 001:FR-087 outcome only)

- The header check is **not** changed. What changes is the outcome for one case: a header-less
  file at the recorded path that **git tracks** is adopted instead of being `ownership_conflict`
  (in `up`) or `failed` (in `down`/`gc`).
- Containment: (1) `gitutil.is_tracked` true is required; a tracked file is adopted whether or
  not it starts with wtenv's header, because a tracked file must never be rewritten or deleted
  (safety L2), and an untracked header-less file is never adopted; (2) adoption never writes, renames, deletes or chmods the file: it only changes the
  registry; (3) an untracked header-less file is `ownership_conflict` exactly as 001 says; (4)
  `down`/`gc` drop the record only when the project step did not fail, as for the override
  today; (5) the adopted path is dropped from the entry's `exclude_patterns` (git ignores
  exclude rules for tracked files, so this changes nothing on disk).
- Tests: new `test_an_untracked_headerless_file_at_the_recorded_path_is_still_an_ownership_conflict`
  (fails if the tracked check is removed); new
  `test_a_tracked_headerless_file_at_the_recorded_path_is_kept_by_down_and_reported_in_kept_items`
  (fails if the outcome falls back to deletion or `failed`); and
  `test_a_recorded_override_that_is_not_wtenvs_is_never_deleted_by_down_or_gc` (kept: untracked case).

### S4. Deleting the isolation file in `down`, `gc`, and `up` without `[compose]`

Unchanged `_override_step` / `_remove_override`: recorded form, `worktree_exists` recheck in
`gc`, symbolic link, header, `removing` first. One guard **extension**:
`compose.override_problem` now accepts the exact relative path `.wtenv/compose.isolation.yaml`
as a name wtenv records, in addition to the four override names. Nothing else under `.wtenv/`
is accepted.

- Tests: `test_a_recorded_path_of_a_form_wtenv_never_records_is_left_alone` (kept), extended
  with `.wtenv/notes.yaml`, `.wtenv/sub/compose.isolation.yaml` and `../.wtenv/compose.isolation.yaml`,
  each left alone; new `test_down_removes_the_isolation_file_and_never_the_repository_override`;
  a symlinked `.wtenv/` or isolation file is `failed` in `down` and `gc`, its target untouched
  (tasks T065, T110).

### S5. Environment injection by `exec` and post-up commands (FR-004, FR-033; relaxes 001:FR-030 only in exec-only worktrees)

- Applies only when the entry records `repository_override`. Otherwise `exec` gives the command
  the v0.1.1 environment, byte for byte (`test_exec_in_an_automatic_worktree_sets_exactly_the_section`),
  and runs only checks A (an inherited `COMPOSE_FILE` naming another worktree's isolation file;
  safety M1) and B (automatic arrangement: an override name appeared, or the recorded override is
  a link or lost its header; safety M5) of data-model.md. Post-up commands get check A too
  (below).
- Fail closed: every FR-033 condition is checked before `os.execvpe`. On any failure the command
  is not run, and the exit is 125 with `isolation_unavailable`. One test per `reason`, each
  asserting that a sentinel command did **not** run (it would create a file).
- What is injected: `COMPOSE_FILE` (three absolute paths), `COMPOSE_PATH_SEPARATOR=:`, and
  `COMPOSE_ENV_FILES` (the `.env` beside the compose file, or `/dev/null`), so a `.env` in the
  child's current directory cannot set the project name (research R7; safety M2). An inherited
  `COMPOSE_ENV_FILES` is refused by check 1.
  **Not** `COMPOSE_PROJECT_NAME`, so that a child with its own `-f` cannot start other files
  under wtenv's project label, which `down` would remove (R2). Test:
  `test_exec_never_sets_compose_project_name`.
- The isolation file's `name:` is the recorded project, checked by `project_problem` in `up`
  (001:FR-088, kept).
- Post-up commands get the same environment from the same builder, built inside `_run_post_up`
  from the entry, so the repeat-`up` call site (nothing changed) gets it too (safety M6). Test:
  `test_post_up_commands_get_the_same_compose_environment_as_exec`, with a repeat-`up` case.
- Check A also runs in `_run_post_up`, in every worktree, before the first command and at both
  call sites (decided by the maintainer, Decisions to review 4). On a match `up` raises
  `isolation_unavailable` (exit 20, `compose_env_override`), runs no post-up command, and marks
  the entry `incomplete` as a failing post-up command does (001:FR-037). With `[compose]`,
  `check_environment` already refuses any `COMPOSE_FILE` earlier (`unsupported`), so the check
  can fire only in a worktree without `[compose]`. Tests T148; mutation check T150.
- Containment of the 001:FR-030 relaxation: it happens only when a repository-owned override
  exists (detected, never configured); `up` reports it in text, JSON and a warning; the user
  documentation states that a plain `docker compose` runs **without** isolation, and that such
  a stack is named by Compose's own default (the directory name), is not recorded, and is never
  removed by `wtenv down`. It can collide with the main checkout's stack when both directories
  have the same name. wtenv cannot prevent that, so the documentation says so.

### S6. Database mode teardown (FR-023)

- No new teardown code. A compose-hosted database is not recorded as a `DatabaseRecord`, so
  `down` never connects to it or drops anything by name. Its container and its `<project>_`
  volume go with the recorded project under 001:FR-039. The label filter, the `<project>_`
  prefix rule (reading R8) and the unlabelled-volume rule (reading R6) are unchanged.
- `up` refuses storage that is not specific to the project (FR-027), so the teardown never
  meets a database volume shared with another worktree or the main checkout. Even if it did
  (a hand-edited compose file after `up`), the R8 prefix rule keeps it.
- Tests: `test_a_labelled_volume_with_a_fixed_name_is_kept_and_the_projects_own_is_removed`
  (kept); new integration `test_down_removes_the_database_container_and_its_project_volume_only`
  (planted decoys: a fixed-name volume, an external volume and another project's volume, each
  created by the test, all surviving).

### S7. Registry compatibility (FR-029)

`repository_override` is serialised only when set. A worktree in the automatic arrangement is
saved byte for byte as v0.1.1 saves it. Test: new `test_a_v0_1_1_registry_is_saved_byte_for_byte`
(golden v0.1.1 registry with compose, Postgres and SQLite entries → load → save → identical bytes).
`test_a_volumes_list_added_to_the_compose_record_makes_the_registry_unreadable` (kept) shows
that unknown fields still make a registry unreadable (001:FR-070).

### Relaxations of spec 001, summarised

| 001 requirement | Relaxed? | Where | Containment |
|-----------------|----------|-------|-------------|
| FR-030 (plain Compose isolated) | **Yes**, exec-only worktrees only (FR-004) | S5 | Detected, reported (text, JSON, warning), documented; automatic arrangement unchanged and tested |
| FR-087 (header check) | **Outcome only**, git-tracked header-less file at the recorded path (FR-006) | S3 | Never written or deleted; untracked case unchanged |
| FR-056, FR-057 (`exec` runs the command; only "not provisioned" refuses) | **Yes**, exec-only worktrees (FR-033); in every other worktree only checks A and B (safety M1, M5); check A also stops `up` before its post-up commands (001:FR-036 extended) | S5 | Every command is refused only when the isolation settings cannot be applied, or by checks A and B; the automatic arrangement's environment unchanged and tested |
| FR-031 (ports tied to variables) | **Narrowed**: the three setups of the FR-028 exception are refused (FR-012) | R5 | `port_variable_conflict` before step 8, nothing changed; named in the CHANGELOG |
| FR-018, FR-039, FR-067, FR-070, FR-086, FR-088 | No | S1–S7 | Applied to the isolation file unchanged |

## Test strategy

**Failing test first, per task group.** Each task group starts with tests that fail for the
right reason (an assertion, not an import error), then the code. The task groups the tasks
should follow, in dependency order:

1. Contracts: `json_models.py` (002), error codes 20–23, warnings, models.
2. Registry: the optional field and the byte-identical v0.1.1 round trip.
3. Port variables (US2): rule, collisions, env-file order, `exec`, `ls`/`up` views. Independent
   of US1 and shippable alone (spec: "it also works on its own").
4. Override inventory and arrangement (US1, `up` side): classification, `override_ambiguous`,
   adoption, transitions, isolation-file write and verification.
5. `exec` and post-up (US1, FR-033): `isolation.py`, every failure reason, no command run.
6. Teardown (US1): `kept_items`, isolation file removal, the repository override never touched.
7. Compose-hosted database (US3): config, URL placeholders, database port, storage classifier,
   warnings, `database_storage_shared`.
8. Documentation (FR-004, FR-025, rule of FR-011) and roadmap entries.
9. Acceptance: quickstart.md on the fastapi template (SC-001 to SC-004).

**Mutation check per guard.** For every guard in the Safety section, the task group's last task
removes the guard in a scratch commit (`git stash` afterwards; never committed), runs the named
test, and records in the task that it failed. The guards: header check before rewrite, before
delete; record-before-create; unrecorded-file refusal at the isolation path; symlink refusal of
`.wtenv/`; exact-path acceptance in `override_problem`; `is_tracked` in adoption; each FR-033
exec check; `COMPOSE_PROJECT_NAME` not injected; R8 prefix rule; FR-027 classifier rows
(external, fixed name, device, outside bind); collision check; `exclude_if` on the new field.
Added by the safety review fixes (tasks.md, "Safety review fixes"): `-p` in the storage
classifier's resolution; the non-absolute device row; checks A and B; check A before post-up
commands; the widened `.env` pattern; `COMPOSE_ENV_FILES`; post-up isolation at both call sites; the excluded-override
warning; the header recheck after the `removing` save; `is_tracked` for a header-carrying file;
the override-name rule of check 2; FR-002 in `up`; percent-encoding of `{compose_env:}`; the `:`
path check; the inventory's refusal of an unrecorded file with wtenv's header.

**Integration tests use only what they create.** Compose projects, volumes and networks in new
tests are named `wtenv-test-<random>`, or are wtenv projects of worktrees created by the test.
Volumes the tests plant (fixed-name, external decoys) are created by the test, carry a random
suffix, and are removed in a `finally`. No test lists or removes resources it did not create,
except to assert they are untouched. Docker tests skip with "Docker is not available" as
today. The `minimums` CI job runs every new integration test on Compose 2.24.4. One test pins
R1 there: `test_the_isolation_file_replaces_the_repository_overrides_ports_on_this_compose`
(starts a two-service stack with a repository override and asserts, with `docker ps`, that
only block ports are published).

**Existing tests whose expected outcome this feature changes** (rewritten in group 4, each
traced to the FR that changes it; no other existing test may change):

| Test | Old expectation | New expectation | Why |
|------|-----------------|-----------------|-----|
| `tests/unit/test_compose_checks.py::test_any_of_the_four_override_names_that_is_not_recorded_is_an_ownership_conflict` | `ownership_conflict` | exec-only arrangement | FR-001 |
| `tests/unit/test_compose_checks.py::test_a_second_override_name_beside_the_recorded_one_is_a_conflict` | `ownership_conflict` | wtenv's own removed, repository's honoured | FR-007 |
| `tests/integration/test_us3_compose.py::test_a_developers_override_is_an_ownership_conflict_and_is_not_modified` | exit 11, file unmodified | exit 0, exec-only, file byte-identical | FR-001, FR-002 |

**Coverage.** `isolation.py` joins the per-module coverage gates (80 %) in CI next to the
existing core modules.

## Roadmap entries (Principle X)

To be added to `docs/roadmap.md` by a task in group 8, not built:

- Setting `--project-directory` equivalents so that Compose 2.24.4 reads the repository's `.env`
  from any subdirectory (R2, known limit).
- Showing a compose-hosted database in `ls` (it is not recorded; `ls` shows its port and
  variable).
- Inspecting the image's `VOLUME`/`PGDATA` to narrow FR-026/FR-027 to the data directory (R4).
- Naming two host addresses of one container port apart (R5 refuses them).

## Points the maintainer should confirm

None of these blocks planning, and each is a decision taken inside the spec. They are listed
so the review can overturn them before `/speckit-tasks`:

1. **New codes rather than new `unsupported` reasons** for FR-007, FR-012, FR-027 and FR-033
   (exit 20 to 23). The alternative, new reasons under `unsupported`, is also allowed by FR-031.
2. **`WTENV_PORT_` prefix** in the variable names (R5).
3. **One container port published twice** (for example by a repository override that adds a
   second host port, E1.6b) is refused by `port_variable_conflict`, as the spec's edge case
   says. A compose setup that v0.1.1 accepted can therefore fail after the upgrade. FR-028 names
   this as one of its three exceptions (with colliding service names and a `ports` entry equal
   to a derived name); it is a visible change, listed in the CHANGELOG.
4. **Every writable mount of the database service counts as data** (R4), which can refuse a
   writable bind outside the worktree that is not the data directory. The fix for the user is
   `:ro`.
5. **`{compose_env:NAME}` values are percent-encoded** into the URL. `{env:NAME}` values stay
   verbatim as in v0.1.x.

## Complexity Tracking

No Constitution Check violation. Nothing to justify.

## Decisions to review

Taken while applying the safety review fixes (tasks.md, "Safety review fixes"). Each one says
what changes for someone who uses v0.1.1 today.

1. **M3, an untracked repository override hidden by wtenv's exclude block.** The fix is the
   report's smallest one. In the exec-only arrangement, when the repository's override is
   untracked, `up` runs `git check-ignore -v --no-index -- <path>`. When the matching line is
   inside wtenv's block (a line written for another worktree, such as `/compose.override.yaml`),
   `up` succeeds and adds the new warning `compose_override_excluded` (`file`, `pattern`). The
   README says why: git then treats the file as ignored, so a checkout of a branch that tracks
   that path overwrites it silently, and `git clean -X` deletes it. wtenv does not remove the
   line, because the other worktree still needs it, and git has no per-worktree exclude file.
   **For v0.1.1 users**: nothing is refused and no file or exclude line changes. The new
   warning appears only in a worktree that uses the exec-only arrangement, which v0.1.1 refused
   with `ownership_conflict`. A main checkout that v0.1.1 already exposes the same way, and that
   is never `up`-ed in the exec-only arrangement, gets no warning; the README is the only
   notice there. It is a new warning code (schema version unchanged; FR-031).
2. **M2, `up` refuses more `.env` lines.** Check 1 and `up`'s `check_environment` share one
   pattern, so `up` now also refuses `COMPOSE_PROJECT_NAME: x`, `export COMPOSE_FILE: x` and a
   first line behind a byte-order mark. **For v0.1.1 users**: a repository whose `.env` sets
   either variable in one of those forms, which v0.1.1 provisioned (and left un-isolated,
   research R7), now gets `unsupported` (`compose_env_override`) from `up`, in both
   arrangements. Nothing else changes.
3. **M2, `COMPOSE_ENV_FILES`.** In the exec-only arrangement only, `exec`, post-up commands and
   `up`'s verification set it to the `.env` beside the compose file (or `/dev/null`), and an
   inherited `COMPOSE_ENV_FILES` makes `exec` fail, as an inherited `COMPOSE_FILE` already does.
   Effect inside `exec`: a Compose command run from a subdirectory now interpolates from the
   project's `.env` (Compose 2.24.4 used the subdirectory's only; 5.1.4 merged both). **For
   v0.1.1 users**: none; the automatic arrangement is untouched (spec, Known limits).
4. **M1, post-up commands. Decided (2026-10-10): applied.** Check A also applies to post-up
   commands, in every worktree: `up` fails with `isolation_unavailable` (exit 20) before the
   first post-up command runs, at both call sites, and runs none. The entry is left
   `incomplete` and the resources in place, as after a failing post-up command (001:FR-037);
   that last part is the one detail the decision did not name, so please check it. With
   `[compose]`, `up` already refuses any `COMPOSE_FILE` (`unsupported`), so only a worktree
   without `[compose]` can reach the new refusal. **For v0.1.1 users**: a worktree without
   `[compose]` whose `up` is run from a shell that `wtenv exec` started in another exec-only
   worktree now fails before its post-up commands, where v0.1.1 ran them with that
   `COMPOSE_FILE` inherited. Tasks T148, T149, T150.
5. **M5, what "no longer matches the record" means in the automatic arrangement.** Check B
   refuses when another override name exists beside the compose file, or when the recorded
   override is a symbolic link or lacks wtenv's header (a repository file now at wtenv's path).
   A **missing** recorded override is not refused: v0.1.1 runs the command then, and the
   decision names only an override that appeared. **For v0.1.1 users**: `exec` in an automatic
   worktree can now fail with exit 125 in those two cases, where v0.1.1 ran the command
   un-isolated.
6. **M4, the `storage` value.** A non-absolute device is reported as `database_storage_shared`
   with the new `storage` value `device_not_absolute` and `name` the device as written. A new
   `details` value is allowed by FR-031. No v0.1.1 effect (compose databases are new).
7. **H1, `-p` only with `[database] type = "compose"`.** The resolution command gains
   `-p <project>` only in that mode, so v0.1.1 configurations resolve exactly as before
   (FR-008, FR-028). The v0.1.1 warning `compose_fixed_volume_name` keeps comparing against the
   repository's own model name, so it keeps the same blind spot for other services; that is
   proposed for the roadmap (tasks.md, "Deferred").
8. **L2, a tracked file at the isolation path.** A git-tracked `.wtenv/compose.isolation.yaml`
   is refused by `up` (`ownership_conflict`), with or without wtenv's header, and kept by
   `down` and `gc` (`kept_items`). That matches the rule the spec already had for a header-less
   file there. **For v0.1.1 users**: a v0.1.1 worktree whose generated `compose.override.yaml`
   was force-added to git is now adopted (`compose_override_adopted`) instead of rewritten, and
   `down` keeps it instead of deleting it.
