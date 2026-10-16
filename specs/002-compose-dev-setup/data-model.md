# Data model: Compose dev setups (spec 002)

**Feature**: `002-compose-dev-setup` | **Date**: 2026-10-10

This document states only what spec 002 adds to or changes in
[001 data-model.md](../001-worktree-runtime-isolation/data-model.md). Everything not mentioned
here is unchanged.

## Registry

`REGISTRY_VERSION` stays **1**. No migration (FR-029; research R6).

### Compose record (`ComposeRecord`): one optional field

| Field | Rules |
|-------|-------|
| `project` | Unchanged |
| `file` | Unchanged |
| `override` | wtenv's generated compose file, relative to the worktree root. **Automatic** arrangement: the auto-loaded override beside the compose file (v0.1.x). **Exec-only** arrangement: exactly `.wtenv/compose.isolation.yaml`. |
| `override_state` | Unchanged: a resource state of the file named by `override` |
| `repository_override` *(new, optional)* | The repository-owned override file the worktree uses, relative to the worktree root, in the plain form of `identity.recorded_path_problem`. Set **only** in the exec-only arrangement. wtenv reads it; nothing ever writes, deletes or excludes it. |

- Serialisation: `repository_override` is written to `registry.json` **only when it is set**
  (`Field(default=None, exclude_if=lambda value: value is None)`). An entry in the automatic
  arrangement is saved byte for byte as v0.1.1 saves it, so v0.1.x can still read a registry in
  which no worktree uses the new arrangement.
- Consistency rule, checked where the record is used (not by the model, so that a hand-edited
  registry is reported rather than unreadable): `repository_override` is set if and only if
  `override` is `.wtenv/compose.isolation.yaml`, **except in state `removing`**: when `[compose]`
  is removed in the exec-only arrangement, the `removing` save clears `repository_override` while
  `override` still names the isolation file that is being removed (T041).
  **The exec-only arrangement is decided by `repository_override` being set, never by the path
  in `override`.** Every `exec` check (FR-033) runs when the entry records `repository_override`;
  such an entry whose `override` is not the isolation path is `isolation_unavailable`
  (`recorded_value_invalid`, check 2). An entry without `repository_override` is not exec-only,
  whatever `override` holds; for it `exec` runs only checks A and B below. `down` and `gc` look
  only at `override`.
- Downgrade (spec, Assumptions): v0.1.x fails with `registry_unreadable` on a registry that holds
  `repository_override`, and changes nothing (001:FR-070).

### What is deliberately not recorded

| Fact | Why not |
|------|---------|
| Port variable names | Derived from `PublishedPort.service`, `target`, `protocol` by the rule (FR-011) whenever needed |
| The compose-hosted database service, port or URL | wtenv creates nothing for it that it must later remove (FR-022, FR-023). The URL can hold a password (FR-020). `up` recomputes all three from the configuration and the compose model |
| A `DatabaseRecord` for `type = "compose"` | No database is created by wtenv. `DatabaseRecord.kind` keeps `postgres` and `sqlite` only |

## Compose arrangement (computed by `up`, never configured)

| Arrangement | Condition (beside the compose file, the four auto-loaded override names) | wtenv's file |
|-------------|----------|--------------|
| `automatic` | No repository-owned override | Auto-loaded override, v0.1.1 bytes (FR-008) |
| `exec_only` | Exactly one repository-owned override | `.wtenv/compose.isolation.yaml` (FR-003, FR-009) |
| *(refused)* | Two or more repository-owned overrides | `override_ambiguous` (FR-007) |

### Override inventory (step 5)

Each existing path among the four override names beside the compose file is classified once:

| Path | Header line | Git tracks it | Class |
|------|-------------|---------------|-------|
| = `ComposeRecord.override` | present | no | `wtenv_own` |
| = `ComposeRecord.override` | present | yes | `adopt` (FR-006): counted as repository-owned; a tracked file is never written or deleted, whatever its header |
| = `ComposeRecord.override` | absent | yes | `adopt` (FR-006): counted as repository-owned |
| = `ComposeRecord.override` | absent | no | `ownership_conflict`, as 001:FR-087 |
| any other of the four names, a regular file | present | any | `ownership_conflict`, as v0.1.1: wtenv's header means wtenv wrote it, for example before the registry was lost |
| any other of the four names, a regular file | absent | any | `repository_owned` (spec, Terms: "wtenv did not create and has not recorded") |
| any other of the four names, a symbolic link | not read | any | `repository_owned` (spec, Edge cases): kept, never written, deleted, excluded or followed for writing |

A symbolic link at the recorded path, or at `.wtenv/compose.isolation.yaml` or `.wtenv/`, fails
closed first, by the step-5 link check (001:FR-086, `_check_override_links`), and no header is
read through it (FR-009). Only a link at an unrecorded override name is accepted, as the
repository's override.

`repository_owned` + `adopt` count | Result
---|---
0 | `automatic`
1 | `exec_only`; every `wtenv_own` file is removed in step 11 (FR-007)
≥ 2 | `override_ambiguous`, naming every file; nothing changes

### Transitions (registry saves in order; step 8 then step 11)

`ComposeRecord` holds one `override`, so a change of path is two saves, as for a moved compose
file in v0.1.x (`_compose_record`, then the path-change branch of `_compose_step`). Every save
keeps the consistency rule above: `repository_override` changes only in the save that also
changes `override`, or, when `override` stays the isolation path, in step 8.

| From | To | Save in step 8 | Step 11, in order |
|------|----|----------------|-------------------|
| none | `automatic` | v0.1.1 | v0.1.1 |
| none | `exec_only` | new record: `override = .wtenv/compose.isolation.yaml` (`creating`), `repository_override = <path>` | write + verify the isolation file; save `created` |
| `automatic` | `exec_only` (a repository override appeared under **another** name) | the old record, fields unchanged (`repository_override` absent), as `removing` | `_remove_override` on the old file (header rechecked immediately before the unlink); save the new record: `override = .wtenv/compose.isolation.yaml` (`creating`), `repository_override = <path>`; write + verify; save `created` |
| `automatic` | `exec_only` by adoption (FR-006: the recorded path now holds a tracked file, header-less or with wtenv's header) | one save that replaces the record: `override = .wtenv/compose.isolation.yaml` (`creating`), `repository_override = <old override path>`; the old path's pattern leaves `exclude_patterns`; warning `compose_override_adopted`. The old record is **never** marked `removing` | the path-change branch is **not** taken (`override` already equals the plan's); write + verify the isolation file; save `created`. The adopted file is **not** touched |
| `exec_only` | `automatic` (the repository override disappeared; FR-008, spec Edge cases) | the old record, fields unchanged (`repository_override` still set), as `removing` | `_remove_override` on the isolation file (header rechecked); save the new record: auto-loaded override (`creating`), no `repository_override`; write + verify; save `created` |
| `exec_only` | `exec_only`, another repository override name | the record with `repository_override` updated; `override` unchanged | isolation file rewritten if its text changed |
| any | no `[compose]` | unchanged from v0.1.x (`removing`) | wtenv's recorded file removed (header rechecked); the `removing` save also clears `repository_override`, so the worktree is no longer exec-only; the record itself stays until `down` (FR-065) |

An interrupted `up` leaves a record that is `creating` or `removing`. The next `up` finishes it
through the same branches as in v0.1.x (001 data-model.md, Resource states).

## Isolation settings file

`.wtenv/compose.isolation.yaml` (layout in [contracts/files.md](contracts/files.md)):

- Same text as the v0.1.x generated override (`compose.override_text`), from the **combined**
  model of the compose file and the repository's override. Every published port of the combined
  model has an entry, and every field Compose reported is repeated with only `published`
  changed (FR-003).
- Never loaded by Compose on its own (its name is not an override name). Loaded through
  `COMPOSE_FILE` by `exec`, post-up commands and `up`'s verification.

### Isolation environment (`isolation.compose_environment`)

The environment `exec`, post-up commands and `up`'s verification add, in the exec-only
arrangement only:

| Variable | Value |
|----------|-------|
| `COMPOSE_FILE` | `<root>/<file>:<root>/<repository_override>:<root>/.wtenv/compose.isolation.yaml` (absolute paths, `<root>` = recorded worktree path) |
| `COMPOSE_PATH_SEPARATOR` | `:` |
| `COMPOSE_ENV_FILES` | `<root>/<dirname(file)>/.env`, the file check 1 read, when it exists; otherwise `/dev/null`. Compose then reads no other `.env`, from any directory (research R7, E6.3b, E6.3c) |

`COMPOSE_PROJECT_NAME` is never set (research R2).

### `exec` checks (FR-033), in order; the first failure stops `exec` before the command runs

| # | Check | `reason` of `isolation_unavailable` |
|---|-------|-------------------------------------|
| 1 | `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` or `COMPOSE_ENV_FILES` non-empty in the calling environment, or `COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` set in `.env` beside the compose file (the same rule as `up`'s `check_environment`; the line pattern accepts `=` or `:` after the name and an optional leading byte-order mark: `\ufeff?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)`, research R7) | `compose_env_override` (`variable`; `path` is the `.env` file when the value comes from it) |
| 2 | `override` is exactly the isolation path, `repository_override` and `file` pass `recorded_path_problem`, and `repository_override` is exactly `dirname(file)/` plus one of the four override names (001 files.md, Names) | `recorded_value_invalid` |
| 3 | No symbolic link from the root down to the isolation file (`identity.symlinked_part`) | `symlink` (`path` is the link) |
| 4 | The isolation file exists | `isolation_file_missing` |
| 5 | Its first line is wtenv's header | `isolation_file_not_wtenvs` |
| 6 | The recorded repository override exists (`os.path.lexists`) | `repository_override_missing` |
| 7 | No other of the four override names exists beside the compose file | `override_appeared` (`path`) |

The worktree must still be `provisioned` first (001:FR-057, unchanged). In an automatic
worktree checks 1 to 7 do not run, the command's environment is v0.1.1's, and `exec` runs only
the checks below (FR-033), with the same error code, exit status 125 and nothing run.

### `exec` checks in every other worktree (FR-033)

| # | Applies to | Check | `reason` of `isolation_unavailable` |
|---|------------|-------|-------------------------------------|
| A | every provisioned worktree that is not exec-only (with or without `[compose]`); `exec`, and `up` before its first post-up command (both call sites) | No entry of the inherited `COMPOSE_FILE` (split on the inherited `COMPOSE_PATH_SEPARATOR`, default `:`) ends in `/.wtenv/compose.isolation.yaml` and differs from `<root>/.wtenv/compose.isolation.yaml`. Any other `COMPOSE_FILE` is passed through as in v0.1.1 | `compose_env_override` (`variable` `COMPOSE_FILE`; `path` the other worktree's isolation file). For `up`: exit 20, the entry `incomplete`, resources left in place, no post-up command run |
| B | the automatic arrangement: a compose record without `repository_override`, whose `override` is an auto-loaded name and whose state is `created` (in any other state, `removing` after `[compose]` was removed for one, the developer's own override files are theirs and `exec` must not refuse them) | No other of the four override names exists beside the compose file (`os.path.lexists`); the recorded override, when it exists, is not a symbolic link and starts with wtenv's header line. A missing recorded override is not checked (v0.1.1) | `override_appeared` (`path` the file that appeared, or the recorded one); `symlink` (`path` the link) |

## Published port and port variable (Story 2)

| Element | Rule |
|---------|------|
| Published port | `PublishedPort` as recorded (unchanged) |
| Port variable name | `WTENV_PORT_<SERVICE>_<TARGET>_<PROTOCOL>`: `SERVICE` = service name in upper case with every character outside `A–Z0–9` replaced by `_`; `TARGET` decimal; `PROTOCOL` `TCP`/`UDP` (research R5) |
| Value | The assigned host port (`PublishedPort.port`) |
| Uniqueness | Over the combined model: no two published ports and no configured variable (`ports` entries, `DATABASE_URL` when `[database]` is set) may share a name. Otherwise `port_variable_conflict` before step 8 |

### Env-file section order (FR-013)

1. The `ports` variables of `wtenv.toml`, in their order (unchanged).
2. The port variables, sorted by (service, container port, protocol). Unique by the uniqueness
   rule, so the order is total.
3. `DATABASE_URL`, when a database is configured.

## Configuration: `[database]` with `type = "compose"` (Story 3)

Details in [contracts/config.md](contracts/config.md).

| Key | `postgres`, `sqlite` | `compose` |
|-----|----------------------|-----------|
| `type` | unchanged | `"compose"` |
| `template` | required (unchanged) | **not allowed** (FR-017) |
| `service` *(new)* | **not allowed** | required: a compose service name |
| `url` | unchanged | required: Postgres scheme, local host (001:FR-025), `{port}` as the port; placeholders `{port}`, `{env:NAME}`, `{compose_env:NAME}` |

`type = "compose"` requires `[compose]`. Otherwise `config_invalid` with `setting`
`database.type`.

### Compose-hosted database (in memory, built at every `up`)

| Field | Source |
|-------|--------|
| `service` | `database.service` |
| `port mapping` | The **one** published port of that service in the combined model (FR-021). Zero or two or more ports, or no such service: `config_invalid`, `setting` `database.service` |
| `host port` | Its assigned `PublishedPort.port` |
| `port variable` | Its Story 2 name |
| `url` | The pattern with `{port}` → host port, `{env:NAME}` → the process environment (verbatim, v0.1.x), `{compose_env:NAME}` → the service's resolved `environment[NAME]`, percent-encoded. A missing name: `config_invalid` (`variable`). Never printed, never recorded (FR-020) |
| `storage` | Classification of the service's data mounts (below) |

### Data storage classification (FR-026, FR-027; research R4)

Data mounts: the service's `volumes` entries of type `volume` or `bind` that are not
`read_only`. Volume names are judged on the model resolved with `-p <project>`, the recorded
project or, on a first `up`, the generated one: `<project>` below is that name. In that model a
project-scoped volume is named `<project>_<key>` and an explicit `name:` is kept as written, so
`name: app_pgdata` in a repository whose own model is named `app` is a fixed name (safety
finding H1). Step 5 adds `-p <project>` to the resolution command only when `[database] type =
"compose"`; otherwise the command is v0.1.1's.

| Mount | Class | Result |
|-------|-------|--------|
| none at all | `no_volume` | warning `compose_database_data_kept` |
| `volume` without `source` | `anonymous_volume` | warning `compose_database_data_kept` |
| `volume` → top-level `external: true` | `external_volume` | `database_storage_shared` |
| `volume` → top-level `name` ≠ `<project>_<key>` | `fixed_name_volume` | `database_storage_shared` |
| `volume` → top-level `driver_opts.type` `nfs`, `cifs` or `none` (local bind), `driver_opts.device` = D not an absolute path (not resolved against any directory) | `device_not_absolute` | `database_storage_shared` (`name` D as written) |
| `volume` → top-level `driver_opts.device` = D, absolute | judged as a bind of D | see the two bind rows |
| `volume` → `<project>_<key>`, no device | project-scoped | accepted, no warning |
| `bind`, `realpath(source)` inside `realpath(root)` | `worktree_directory` | warning `compose_database_data_kept` |
| `bind`, `realpath(source)` outside `realpath(root)` | `host_directory` | `database_storage_shared` |

The first refusal found, in mount order, is reported. Warnings are collected for every accepted
mount.

## Output models (summary; full contract in [contracts/json_models.py](contracts/json_models.py))

| Model | Addition |
|-------|----------|
| `PortView` | `port_variable: str \| None` (the Story 2 name; set for every published port) |
| `WorktreeView` | `compose_isolation: "automatic" \| "exec_only" \| None`; `compose_repository_override: str \| None` |
| `UpResult` | `compose_database: ComposeDatabaseView \| None` (service, target, protocol, port, port_variable, url_variable) |
| `DownResult`, `GcResult` | `kept_items: list[KeptItem]` (`KeptItem` = `Item` + `reason: "repository_owned"`) |
| `ErrorCode` | `isolation_unavailable` (20), `override_ambiguous` (21), `port_variable_conflict` (22), `database_storage_shared` (23) |
| `WarningCode` | `compose_isolation_exec_only`, `compose_database_data_kept`, `compose_override_adopted`, `compose_override_excluded` |

`SCHEMA_VERSION` stays 1: every change is an addition (001 json_models.py: "New codes may be
added; existing ones never change meaning").
