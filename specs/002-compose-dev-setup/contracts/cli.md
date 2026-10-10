# Contract delta: the `wtenv` command line (spec 002)

**Feature**: `002-compose-dev-setup` | **Contract version**: 1 (additions only) | **Date**: 2026-10-10

This file lists only what spec 002 changes in
[001 contracts/cli.md](../../001-worktree-runtime-isolation/contracts/cli.md). Every rule, code,
exit status, step and output not mentioned here is unchanged. No existing code, exit status or
`reason` changes meaning (FR-031). The JSON documents are in
[json_models.py](json_models.py) (schema version 1, additions only).

## Rules for every command: changed row

| Rule | Detail |
|------|--------|
| Environment variables | wtenv defines none of its own (unchanged). In addition to 001's list, `exec` reads `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` and `COMPOSE_ENV_FILES` in a worktree whose repository owns its override (FR-033), and sets `COMPOSE_FILE`, `COMPOSE_PATH_SEPARATOR` and `COMPOSE_ENV_FILES` for the command there. In every other worktree it reads `COMPOSE_FILE` and `COMPOSE_PATH_SEPARATOR` (FR-033, check A). `up` reads the database service's resolved environment through Compose for `{compose_env:NAME}` ([config.md](config.md)). |

## Error codes and exit statuses: new rows

| Exit | Code | Meaning | What the caller can do |
|------|------|---------|------------------------|
| 20 | `isolation_unavailable` | `exec`, in a worktree whose repository owns its override, cannot apply the worktree's isolation settings; the command was not run. **`exec` reports it with exit status 125**, like every failure of wtenv itself (001, `wtenv exec`); 20 is the code's status in the one-code-one-status table and is not seen as a process exit status | See `details.reason`; usually unset the variable, or run `wtenv up` again |
| 21 | `override_ambiguous` | Two or more override files that the repository owns sit beside the compose file; Compose would load only one of them | Keep one of them, then run `wtenv up` again |
| 22 | `port_variable_conflict` | Two published ports would get the same port variable name, or a name equals a variable the configuration defines | Rename a service or a `ports` entry, or publish the container port once |
| 23 | `database_storage_shared` | The compose-hosted database keeps its data where other worktrees or the main checkout would share it | Store the data in a project volume (no `name:`, not `external`) or a directory inside the worktree |

### Error details: new and changed rows

Keys may be added; none is removed or renamed (unchanged rule).

| Code | Keys |
|------|------|
| `isolation_unavailable` | `reason`: `compose_env_override` (with `variable`; `path` is the `.env` file when the value comes from it, or the other worktree's isolation file named by an inherited `COMPOSE_FILE`), `recorded_value_invalid`, `symlink` (`path` is the link), `isolation_file_missing`, `isolation_file_not_wtenvs`, `repository_override_missing`, or `override_appeared` (also in the automatic arrangement); `path` (the file concerned, absolute) |
| `override_ambiguous` | `files` (absolute paths, sorted) |
| `port_variable_conflict` | `variable` (the name); `sources` (two strings, each either `service <name>, container port <n>/<protocol>`, `wtenv.toml ports`, or `DATABASE_URL`) |
| `database_storage_shared` | `service`; `storage`: `fixed_name_volume`, `external_volume`, `device_not_absolute`, or `host_directory`; `name` (the volume's resolved name, the device as written, or the directory's real path) |
| `config_invalid` | unchanged keys; new `setting` values `database.service` and `database.type`; `variable` also for a `{compose_env:NAME}` name the service does not have |
| `unsupported` | new `reason`: `compose_path_separator` (`file`: the path that contains `:`) |

## `wtenv up`

**Order of work: changed steps.** Steps 1 to 4, 9 and 14 are unchanged. Steps 5 to 7 still
change nothing.

5. If compose is configured, in this order (additions in **bold**):
   - The recorded project check, the Docker and Compose version checks, the unused-project
     check: unchanged.
   - **Override inventory** ([data-model.md](../data-model.md#override-inventory-step-5)). Every
     existing file among the four override names beside the compose file is classified. A file
     at the recorded override path that lacks wtenv's header line is `ownership_conflict` as in
     001, **unless git tracks it, in which case it is adopted as the repository's (FR-006). A
     tracked file there that still starts with wtenv's header is adopted too: wtenv never
     rewrites or deletes a tracked file.**
     Any other override file is the repository's. **Two or more repository-owned files:
     `override_ambiguous`, naming them (FR-007).** One: the worktree uses the **exec-only
     arrangement**. None: the **automatic arrangement**, exactly as v0.1.1 (FR-008).
   - **Exec-only arrangement only:** `.wtenv/` and `.wtenv/compose.isolation.yaml` must not be
     symbolic links (`env_file_unusable`, `symlink`). A file at `.wtenv/compose.isolation.yaml`
     that the entry does not record as its override is `ownership_conflict` (`kind`
     `compose_override`). A recorded one must start with wtenv's header line (FR-087) **and
     must not be tracked by git (`ownership_conflict`; FR-006)**. **When the repository's
     override is untracked, `git check-ignore -v --no-index -- <path>` is run; a match from a
     line inside wtenv's exclude block gives the warning `compose_override_excluded` (`file`,
     `pattern`; FR-002).** An
     absolute path of the compose file, the repository's override or the isolation file that
     contains `:` is `unsupported`, reason `compose_path_separator`.
   - `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE`: refused in the environment and in `.env`, in
     both arrangements, as in 001. **The `.env` line pattern also accepts `KEY: value` and a
     leading byte-order mark, which Compose honours (research R7; shared with `exec` check 1).**
   - Resolution: `docker compose **[-p <project>]** -f <compose file> **[-f <repository
     override>]** --profile "*" config --format json`. **`-p` is passed only with `[database]
     type = "compose"`, so that the storage check sees volume names as the stack gets them
     (data-model.md, Data storage classification).** A published range in either file is `unsupported`,
     `compose_port_range` (001:FR-033, extended by FR-003).
   - **Port variables: the name of every published port, and their collisions:
     `port_variable_conflict` (FR-012).**
   - **With `[database] type = "compose"`: the service exists and publishes exactly one port
     (`config_invalid`, `setting` `database.service`; FR-021); its data storage (FR-026,
     FR-027): refused with `database_storage_shared`, or accepted with the warning
     `compose_database_data_kept`.**
6. Block size: unchanged, counted over the combined model (FR-015).
7. Database checks: unchanged for `postgres` and `sqlite`. **For `compose`, the URL pattern is
   resolved with a stand-in port, so that an unset `{env:NAME}` or a missing
   `{compose_env:NAME}` fails here. No server is contacted (FR-022).**
8. Registry: unchanged, plus the compose arrangement: `override` is the auto-loaded override or
   `.wtenv/compose.isolation.yaml`, and `repository_override` is set only in the exec-only
   arrangement ([data-model.md](../data-model.md#transitions-step-8-records-them-step-11-carries-them-out)).
   The isolation file's path goes into `exclude_patterns`; the repository's override never does.
10. Database: **nothing for `compose`** (no template copy, no container started; FR-022).
11. Compose: write and verify **wtenv's file for the arrangement**: the auto-loaded override, or
    the isolation file. In the exec-only arrangement the verification runs `docker compose
    --profile "*" config --format json` with the same `COMPOSE_FILE` environment that `exec`
    sets. The project name must be the recorded one and the published ports exactly the
    assigned ones; otherwise the file just written is removed and `up` fails with `unsupported`,
    `compose_verification_failed` (unchanged). **When the arrangement changed, wtenv's previous
    file is removed first, with its header checked immediately before (FR-007, FR-087). A
    repository-owned override is never written, moved or removed (FR-002).**
12. Env file: the section also holds the **port variables** and, for `compose`, the
    **`DATABASE_URL` built with the assigned port** ([files.md](files.md#env-file-section)).
13. Post-up commands: unchanged, **plus, in the exec-only arrangement, the same `COMPOSE_FILE`,
    `COMPOSE_PATH_SEPARATOR` and `COMPOSE_ENV_FILES` as `exec` (FR-033)**. **The same holds for
    the post-up commands of a repeat `up` with nothing changed: `_run_post_up` builds the
    environment from the entry, at both call sites.**

**Text output** (example, exec-only arrangement with a compose-hosted database):

```text
wtenv: provisioned /code/feature-x
  ports      20000-20009  PORT=20000
  published  adminer:8080 -> 20001 (WTENV_PORT_ADMINER_8080_TCP)  backend:8000 -> 20002 (WTENV_PORT_BACKEND_8000_TCP)  db:5432 -> 20003 (WTENV_PORT_DB_5432_TCP)  ...
  database   compose service db on localhost:20003  (DATABASE_URL)
  compose    wtenv-feature-x-3f9a1c2b  .wtenv/compose.isolation.yaml (created)
  isolation  exec only: the repository owns compose.override.yml. Run Compose through
             `wtenv exec -- docker compose ...`; a plain `docker compose` here runs WITHOUT isolation.
  env file   .env.local (updated)
```

A port tied to a `ports` variable shows both names: `db:5432 -> 20011 (DB_PORT, WTENV_PORT_DB_5432_TCP)`.

**JSON**: `UpResult`, with `worktree.compose_isolation`, `worktree.compose_repository_override`,
`worktree.ports[].port_variable`, and `compose_database` (FR-005, FR-014, FR-032).

**Warnings**: 001's list, plus `compose_isolation_exec_only` (every `up` in the exec-only
arrangement; FR-004, FR-005), `compose_database_data_kept` (FR-026, FR-027),
`compose_override_adopted` (FR-006), `compose_override_excluded` (FR-002).

**Exit statuses**: 001's list, plus **21, 22, 23**.

## `wtenv down`

Changes:

- In the exec-only arrangement, the recorded override is `.wtenv/compose.isolation.yaml`. It is
  removed by the same step and under the same checks as the generated override (recorded form,
  symbolic links, header line). Under "Recorded values", the override's file name must be one of
  the four names in 001 files.md, Names, **or the path must be exactly
  `.wtenv/compose.isolation.yaml`** (FR-009).
- The repository's override is never listed in `removed`, `would_remove`, `already_absent`,
  `failed` or `kept_items`, and is never touched (FR-002).
- **FR-006**: when the recorded override path holds a file **that git tracks**, with or without
  wtenv's header line, `down` does not delete it. It is reported in the new list `kept_items` with
  `kind` `compose_override`, `name` its path, and `reason` `repository_owned`, **not** under
  `failed`. The compose record is dropped as for a removed override (only when the project step
  did not fail). With `--dry-run` it is listed in `kept_items` the same way. A header-less file
  that git does not track is still `failed`, as in 001.
- A compose-hosted database adds nothing: its container and its `<project>_` data volume are
  removed with the project, and an anonymous volume is in `kept_volumes` (001, unchanged;
  FR-023).

**JSON**: `DownResult` with `kept_items`. **Exit statuses**: unchanged.

## `wtenv gc`

The same changes as `down`, through the same release code path: the isolation file, the
repository's override never touched, `kept_items` in `GcResult`. **Exit statuses**: unchanged.

## `wtenv ls`

- `worktrees[].ports[].port_variable` is the port variable name of each published port
  (FR-014). The text `PUBLISHED` cell becomes `service:container-port->host-port (NAME)`.
- `worktrees[].compose_isolation` (`automatic`, `exec_only`, or null without compose) and
  `compose_repository_override`.
- A compose-hosted database is not recorded, so the `DATABASE` column shows `-` for it. Its port
  is in `PUBLISHED` with its variable.

## `wtenv exec`

Additions (FR-004, FR-033). In a worktree whose entry records a repository-owned override
(the exec-only arrangement), before the command is run:

1. The worktree must be `provisioned` (unchanged).
2. The variables of wtenv's section are read (unchanged). They now include the port variables
   and, for `compose`, `DATABASE_URL`.
3. **The isolation checks, in the order of
   [data-model.md](../data-model.md#exec-checks-fr-033-in-order-the-first-failure-stops-exec-before-the-command-runs).**
   The first that fails stops `exec` with `isolation_unavailable` and exit status 125. **The
   command is not run.** These checks apply to every command, because `exec` cannot tell which
   commands start Compose (spec, Assumptions).
4. **The command's environment is the current one, plus the section's variables, plus
   `COMPOSE_FILE=<compose file>:<repository override>:<isolation file>` (absolute paths),
   `COMPOSE_PATH_SEPARATOR=:`, and `COMPOSE_ENV_FILES=<the .env beside the compose file>` (absolute;
   `/dev/null` when there is none), so Compose reads no other `.env` (research R7).** `COMPOSE_PROJECT_NAME` is not set: the project name comes from
   the isolation file. Child processes inherit the environment, so a script or `make` target
   that runs `docker compose` is isolated too.

A Compose command that passes its own `-f` is not isolated (Compose then ignores `COMPOSE_FILE`).
One that passes `--env-file` reads that file instead of the checked `.env`.
One that passes `-p` keeps the remapped ports under its own project name, which wtenv does not
record or remove (spec, Edge cases; research R2).

In every other worktree, the command's environment is exactly v0.1.1's, apart from the new
variables in the section. `exec` runs only checks A (every such worktree: an inherited
`COMPOSE_FILE` that names another worktree's isolation file) and B (the automatic arrangement:
no override name appeared beside the compose file, and the recorded override is not a link and
keeps wtenv's header) of data-model.md, with `isolation_unavailable` and exit status 125, the
command not run (FR-033).

**Exit status**: unchanged table (125 for any failure of wtenv itself, including
`isolation_unavailable`).

## `wtenv doctor`

Unchanged (spec, Edge cases: "`doctor` is unchanged by this feature").

## User documentation (README) obligations

The README must state, in plain words (FR-004, FR-011, FR-025):

- In a repository that tracks its own Compose override, Compose commands are isolated **only
  when run through `wtenv exec`**. A plain `docker compose` runs the repository's setup
  **without isolation**: the repository's fixed ports, and a project name Compose derives from
  the directory, which wtenv does not record and `wtenv down` never removes.
- The port variable rule, with the examples of [files.md](files.md#env-file-section).
- The port variables and `DATABASE_URL` are host-side values; a container that receives one
  through Compose interpolation gets the host-side value.
- Compose reads `.env` from the current directory (Compose 2.24.4 only there; Compose 5.1.4
  from the project directory and, when it has one, also from the current directory, whose
  values win; the versions in between were not tested). Through `wtenv exec` in a repository
  that tracks its own override, Compose reads only the `.env` beside the compose file. Anywhere
  else, run Compose from the compose file's directory, and know that a `.env` in another
  directory can change the project name (spec, Known limits; research R2, R7).
- An untracked override file of your own can be ignored by git because of wtenv's exclude
  block, written for another worktree of the same repository; then a checkout or `git clean -X`
  can overwrite or delete it. `up` warns `compose_override_excluded` (FR-002).
