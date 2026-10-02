# Contract: the `wtenv` command line

**Feature**: `001-worktree-runtime-isolation` | **Contract version**: 1 | **Date**: 2026-10-03

This is the complete command surface of wtenv v1 (FR-001). Anything not listed here is not
built; ideas go to `docs/roadmap.md`.

Related contracts: [json_models.py](json_models.py) (the `--json` documents),
[config.md](config.md) (`wtenv.toml`), [files.md](files.md) (files wtenv writes).

```text
wtenv --version [--json]
wtenv up [--json]
wtenv down [--dry-run] [--json]
wtenv gc [--dry-run] [--release PATH]... [--json]
wtenv ls [--json]
wtenv exec [--json] -- COMMAND [ARG]...
wtenv doctor [--json]
wtenv hook install [--json]
wtenv hook uninstall [--dry-run] [--json]
```

## Rules for every command

| Rule | Detail |
|------|--------|
| Input | wtenv never prompts and never reads standard input (FR-004). Only `exec` passes standard input on, to the command it runs. There is no `--interactive` flag in v1. |
| Output without `--json` | The result goes to standard output as text for people. This text is not a stable interface. |
| Output with `--json` | Standard output holds exactly one JSON document, defined in [json_models.py](json_models.py). Everything else goes to standard error (FR-058). |
| Errors | Standard error gets `wtenv: error [<code>]: <message>`, and a `hint: …` line when there is one. With `--json`, the document carries the same code in `error.code`. |
| Warnings | Standard error gets `wtenv: warning [<code>]: <message>`. With `--json`, they are also in `warnings`. |
| Exit status | 0 on success. On failure, the one status of the error code (table below). `exec` is the exception; see its section. |
| Credentials | Never printed, in any mode (FR-019). A database is shown by kind, name, host, and port. |
| Where it runs | `up`, `down`, `exec`, `hook install`, and `hook uninstall` act on the worktree that contains the current directory, from any subdirectory (FR-002). `ls`, `gc`, and `doctor` run anywhere (FR-003). |
| Network | Only the local Postgres server named in `wtenv.toml` and the local Docker engine (FR-025, FR-035, FR-071). |
| Environment variables | wtenv defines none of its own. It reads `XDG_STATE_HOME` (location of the state directory), the variables named by `{env:NAME}` in the database URL pattern, the standard libpq password sources (`PGPASSWORD`, `PGPASSFILE`) during teardown, `DOCKER_HOST`, and `PATH`. |
| Help | `--help` on `wtenv` and on every command. Shell completion is not installed. |

## Error codes and exit statuses

Stable from the first release (Principle IV). One code, one exit status.

| Exit | Code | Meaning | What the caller can do |
|------|------|---------|------------------------|
| 0 | | Success | |
| 1 | `internal_error` | Unexpected failure inside wtenv | Report it; run `wtenv doctor` |
| 2 | `usage_error` | Unknown command, option, or argument | Fix the command line |
| 3 | `config_invalid` | `wtenv.toml` is invalid, or asks for more than the block holds | Fix the setting named in `details.setting` |
| 4 | `not_in_worktree` | The current directory is not inside a git worktree | Change directory |
| 5 | `not_provisioned` | The worktree has no completed `up` | Run `wtenv up` |
| 6 | `no_free_block` | No free port block in the range | Run `wtenv gc`, or `wtenv ls` to see what holds blocks |
| 7 | `env_file_unusable` | The env file cannot be written safely | See `details.reason` |
| 8 | `dependency_unavailable` | git, Docker, or the Postgres server is missing, too old, not local, or refuses the connection | Start or fix the dependency, then run the command again |
| 9 | `template_missing` | The template database or template file does not exist | Create it, or fix `database.template` |
| 10 | `template_in_use` | The template has open connections and cannot be copied | Close them, then run `wtenv up` again |
| 11 | `ownership_conflict` | Something exists where wtenv would create a resource, and wtenv has no record of creating it | Rename or remove it by hand; wtenv will not touch it |
| 12 | `post_up_failed` | A post-up command exited non-zero | Fix the command, then run `wtenv up` again |
| 13 | `partial_failure` | `down` or `gc` could not remove some items; they stay recorded | Fix the cause in `failed[].reason`, then run the command again |
| 14 | `registry_busy` | The registry lock was not free within 10 seconds | Try again |
| 15 | `worktree_busy` | Another `up` or `down` held this worktree for more than 60 seconds | Try again |
| 16 | `registry_unreadable` | The registry cannot be read or locked; nothing was changed | Repair or remove the file named in `details.path` by hand |
| 17 | `problems_found` | `doctor` found at least one problem | Read `findings` |
| 18 | `worktree_exists` | `gc --release` named a worktree that still exists | Run `wtenv down` in that worktree |
| 19 | `unsupported` | The request is valid, but v1 cannot carry it out safely in this setup | See `details.reason` |

### Error details

`error.details` holds these keys. Keys may be added; none is removed or renamed.

| Code | Keys |
|------|------|
| `config_invalid` | `file`; `setting` (dotted name, such as `database.url`); `min_block_size` when the block is too small; `variable` when an `{env:NAME}` variable is unset |
| `not_in_worktree` | `cwd` |
| `not_provisioned` | `path`; `status` (`unprovisioned`, `incomplete`, or `unverifiable`) |
| `no_free_block` | `block_size`; `range` |
| `env_file_unusable` | `path`; `reason`: `is_directory`, `parent_missing`, `not_writable`, `tracked_by_git`, or `markers_damaged` |
| `dependency_unavailable` | `dependency`: `git`, `docker`, or `postgres`; `reason`: `not_installed`, `too_old`, `not_running`, `not_local`, `cannot_connect`, `authentication_failed`, or `permission_denied`; `required` and `found` for `too_old` |
| `template_missing` | `kind` (`postgres` or `sqlite`); `template` |
| `template_in_use` | `kind`; `template`; `connections` (Postgres only) |
| `ownership_conflict` | `kind` (an `ItemKind`); `name` |
| `post_up_failed` | `command`; `exit_status` |
| `registry_busy`, `worktree_busy` | `waited_seconds` |
| `registry_unreadable` | `path`; `reason`: `invalid_json`, `invalid_schema`, `unknown_version`, `not_readable`, or `lock_unsupported` |
| `problems_found` | `problems` (count) |
| `worktree_exists` | `path`; `current_path` when the worktree was moved |
| `unsupported` | `reason`: `compose_port_range`, `compose_port_clash`, `compose_env_override`, `compose_verification_failed`, `hooks_path_redirected`, `hook_not_shell`, `markers_damaged`, or `interrupted_removal`; `service` or `file` where it applies |
| `internal_error` | `exception` (class name) |

## Worktree status

`ls`, `doctor`, `exec`, and `gc` all use the same classification
([data-model.md](../data-model.md#status-and-the-orphan-checks)).

| Status | Meaning |
|--------|---------|
| `provisioned` | The last `up` completed, and the worktree is where the registry says |
| `incomplete` | An `up` or `down` was interrupted or partly failed (this includes a failed post-up command) |
| `unprovisioned` | A worktree of the current repository with no registry entry |
| `orphaned` | All three checks of FR-045 hold; `gc` will release it |
| `unverifiable` | The worktree cannot be found as recorded, but its removal is not confirmed. `reason` is `repository_not_found`, `git_still_lists`, `moved`, or `path_exists` |

---

## `wtenv --version`

Prints `wtenv <version>`. With `--json`, prints a `VersionResult`. Imports nothing but the
command-line framework (NFR-001).

## `wtenv up`

Provisions the current worktree, or brings it up to date with its `wtenv.toml`. Running it
again with nothing changed changes nothing (FR-011, FR-017, FR-023).

**Order of work.** `up` takes the worktree lock, then runs every check that needs no
resource. Nothing is changed unless all of them pass.

1. Identify the worktree. Not inside one: `not_in_worktree`.
2. Take the worktree lock (wait up to 60 s, then `worktree_busy`).
3. Load `wtenv.toml`, or the defaults when there is none. Invalid: `config_invalid`.
4. Check the env file path: inside the worktree, parent directory present, not a directory,
   not tracked by git, writable, markers intact. Otherwise `env_file_unusable`.
5. If compose is configured: check Docker and the Compose version, the compose file's name,
   that no override file of the developer's exists, and that `COMPOSE_PROJECT_NAME` and
   `COMPOSE_FILE` are not set. Resolve the compose file and count the ports it publishes.
6. Check that the block holds all port variables and published ports. Otherwise
   `config_invalid` with `details.min_block_size` (FR-014, FR-032).
7. If a database is configured: check that the URL pattern resolves and names a local host.

   *From here on, `up` changes things, in this order:*
8. Registry: create the entry, or record a new location (FR-084); allocate a block when the
   entry has none or the block size changed (`no_free_block` if none is free); assign
   ports; add the worktree's patterns to `.git/info/exclude`.
9. Database: create it from the template if it is not recorded. Failures here are
   `template_missing`, `template_in_use`, `ownership_conflict`, or `dependency_unavailable`.
10. Compose: write the override file and verify it.
11. Env file: write wtenv's section.
12. Run the post-up commands in order. The first non-zero exit stops `up` with
    `post_up_failed`.
13. Registry: mark the worktree `provisioned`.

A failure in steps 8 to 12 leaves what was created in place and recorded, with the worktree
`incomplete`. Running `up` again continues from there (FR-069).

**Post-up commands** run through `sh -c`, from the worktree root, with the worktree's
variables added to the environment, with standard input closed, and with their standard
output sent to wtenv's standard error. They run on every successful `up` (FR-036).

**Text output** (example):

```text
wtenv: provisioned /code/feature-x
  ports      20010-20019  PORT=20010 DB_PORT=20011
  published  db:5432 -> 20011 (DB_PORT)  cache:6379 -> 20012
  database   postgres wtenv_feature_x_3f9a1c2b on localhost:5432  (created)
  compose    wtenv-feature-x-3f9a1c2b  compose.override.yaml (created)
  env file   .env.local (updated)
```

**JSON**: `UpResult`. `changes` lists each item with `created`, `updated`, `unchanged`, or
`released`.

**Warnings**: `env_duplicate_variable` (FR-080), `compose_fixed_container_name`,
`worktree_moved`.

**Exit statuses**: 0, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 19.

## `wtenv down`

Releases everything the registry records for the current worktree (FR-038).

| Option | Effect |
|--------|--------|
| `--dry-run` | Changes nothing, takes no worktree lock, and lists what `down` would remove (FR-040) |

**Order of work**: compose project (containers, networks, volumes, then the override file);
databases; wtenv's section of the env file (and the file itself when wtenv created it and
nothing else is in it); registry entry and port block; and, when this was the last
registered worktree of the repository, wtenv's entries in `.git/info/exclude` (FR-085).

- SQLite side files (FR-039): each existing `-wal`, `-shm`, or `-journal` file beside the
  recorded SQLite copy appears as its own item in `removed` (or in `would_remove` with
  `--dry-run`), with `kind` `sqlite_file` and `name` the file's absolute path, the same shape
  as the database file's own item. A side file that does not exist is not listed. Side files
  are removed only together with the recorded database file.
- A worktree with no registry entry: success, nothing changed (FR-043).
- A recorded item that is already gone is reported under `already_absent`; that is not an
  error (FR-042).
- An item that cannot be removed stays recorded and is reported under `failed`. The entry
  stays, as `incomplete`, and the exit status is 13. Running `down` again finishes the job.
- `down` uses only the registry. A missing or invalid `wtenv.toml` does not stop it; an
  invalid one produces the warning `config_ignored`.
- The Postgres password for the drop comes from `wtenv.toml` when it resolves, otherwise from
  the libpq sources (`PGPASSWORD`, `~/.pgpass`).

**JSON**: `DownResult`. **Exit statuses**: 0, 4, 13, 14, 15, 16.

## `wtenv gc`

Releases registry entries whose worktrees git confirms are gone, across all repositories
(FR-045 to FR-047, FR-072 to FR-075).

| Option | Effect |
|--------|--------|
| `--dry-run` | Changes nothing and lists what would be removed |
| `--release PATH` | Release the entry whose recorded worktree path is `PATH`, even though it is unverifiable. May be given several times. With this option, `gc` acts **only** on the named entries; it does not sweep (FR-073) |

**Plain `gc`**

1. Read the registry and classify every entry.
2. For each `orphaned` entry: try its worktree lock without waiting. If it is held, skip the
   entry and report it under `skipped_busy`.
3. Holding the lock, classify the entry again. If it is no longer `orphaned`, skip it
   (FR-074).
4. Release it as `down` would. This includes the SQLite side-file items, which are listed
   under `removed` and, with `--dry-run`, under `would_remove`, as for `down`.

Entries that are `unverifiable` are reported under `kept` with their reason. Entries of
existing worktrees are not touched and not listed. Neither `kept` nor `skipped_busy` changes
the exit status.

**`gc --release PATH`**

1. Every `PATH` is checked first. If any names an entry whose worktree still exists, at that
   path or (reason `moved`) at another, the command fails with `worktree_exists` and changes
   nothing.
2. A `PATH` with no entry is reported under `no_entry`. That is not an error, so the command
   can be repeated safely.
3. Each remaining entry is released as `down` would, with the same lock rule as plain `gc`.

**Credentials**: `gc` has no `wtenv.toml` to read. The Postgres password comes from the libpq
sources. Without it, the database is reported under `failed`, stays recorded, and the exit
status is 13.

**JSON**: `GcResult`. **Exit statuses**: 0, 13, 14, 16, 18.

## `wtenv ls`

Lists every entry of the registry, across all repositories, and, when run inside a
repository, that repository's worktrees that are not provisioned (FR-048). Changes nothing
and takes no worktree lock (FR-050).

**Text output** (example):

```text
STATUS         PORTS        VARIABLES              DATABASE                           COMPOSE                   PATH
provisioned    20000-20009  PORT=20000             postgres wtenv_app_91c2d0aa        wtenv-app-91c2d0aa        /code/app
provisioned    20010-20019  PORT=20010             postgres wtenv_feature_x_3f9a1c2b  wtenv-feature-x-3f9a1c2b  /code/feature-x
unverifiable   20020-20029  PORT=20020             postgres wtenv_usb_5d0e77b1        -                         /Volumes/usb/wt  (git_still_lists)
unprovisioned  -            -                      -                                  -                         /code/feature-y
```

**JSON**: `LsResult`. **Exit statuses**: 0, 14, 16.

## `wtenv exec`

Runs a command with the worktree's variables added to the current environment (FR-056).

```text
wtenv exec [--json] -- COMMAND [ARG]...
```

- `--` is required. Everything after it belongs to the command.
- The variables are the worktree's port variables and, when a database is recorded,
  `DATABASE_URL`. The developer's own lines in the env file are not loaded.
- The worktree must be `provisioned`. Otherwise wtenv fails with `not_provisioned` and does
  not run the command (FR-057).
- wtenv replaces itself with the command, so standard input, output, error, and signals reach
  it directly.

**Exit status**: `exec` is a wrapper and follows the convention of `env(1)`.

| Exit | When |
|------|------|
| the command's own status | The command ran |
| 125 | wtenv itself failed (any error code, including `usage_error`) |
| 126 | The command was found but could not be run |
| 127 | The command was not found |

`--json` applies only to wtenv's own failure: an `ExecResult` with the real code in
`error.code` and 125 in `error.exit_status`. When the command runs, wtenv prints nothing.

## `wtenv doctor`

Checks and reports; changes nothing (FR-060, FR-061).

| Finding code | Severity | Reported when |
|--------------|----------|---------------|
| `block_overlap` | problem | Two blocks in the registry share a port |
| `port_conflict` | problem | An assigned port is held by a process outside the owning worktree |
| `port_in_use` | info | An assigned port is in use and the holder cannot be determined |
| `orphaned_worktree` | problem | An entry is `orphaned`; `gc` would release it |
| `unverifiable_worktree` | problem | An entry is `unverifiable`; the reason is in `details.reason` |
| `missing_resource` | problem | A recorded database, file, or section no longer exists |
| `incomplete_worktree` | problem | An entry is `incomplete` |
| `dependency_unavailable` | problem | Docker or Postgres is needed by the current worktree's `wtenv.toml` and is unavailable |
| `resource_check_skipped` | info | A recorded Postgres database could not be checked because the server could not be reached |

`dependencies` lists `git`, `docker`, and `postgres`, each `ok`, `unavailable`, or
`not_required`. A dependency the configuration does not need is `not_required`, and is never
a finding. Outside a repository, `docker` and `postgres` are `not_required`.

Exit status 0 when no finding has severity `problem`; otherwise 17, with `ok` false and
`error.code` `problems_found`.

**JSON**: `DoctorResult`. **Exit statuses**: 0, 14, 16, 17.

## `wtenv hook install`

Installs the auto-provisioning git hook for the repository of the current worktree (FR-051
to FR-053). It is never installed by any other command.

- Writes the block in [files.md](files.md#git-hook-block) into
  `<git-common-dir>/hooks/post-checkout`: directly after the shebang line of an existing
  hook, or in a new executable file.
- Running it again rewrites the block in place; `action` is then `unchanged` or `updated`.
- `core.hooksPath` pointing anywhere else, an existing hook that is not a POSIX shell
  script, or damaged markers: `unsupported`. The error's hint contains the block, for adding
  by hand.

**JSON**: `HookInstallResult`. **Exit statuses**: 0, 4, 14, 16, 19.

## `wtenv hook uninstall`

Removes only what `hook install` added.

| Option | Effect |
|--------|--------|
| `--dry-run` | Changes nothing and lists what would be removed (FR-040) |

- Removes the block. Removes the file too only when the registry records that wtenv created
  it and nothing but the shebang line is left.
- No block present: success, `action` is `absent`.

**JSON**: `HookUninstallResult`. **Exit statuses**: 0, 4, 14, 16, 19.

---

## Examples of `--json` documents

A failed `up` (exit status 10):

```json
{
  "schema_version": 1,
  "command": "up",
  "ok": false,
  "error": {
    "code": "template_in_use",
    "exit_status": 10,
    "message": "template database \"app_template\" has 2 open connections",
    "hint": "Close them, or use a template nothing connects to, then run `wtenv up` again.",
    "details": {"kind": "postgres", "template": "app_template", "connections": 2}
  },
  "warnings": [],
  "worktree": null,
  "changes": [],
  "post_up": []
}
```

A `gc` that released one entry and kept one (exit status 0):

```json
{
  "schema_version": 1,
  "command": "gc",
  "ok": true,
  "error": null,
  "warnings": [],
  "dry_run": false,
  "released": ["/code/old-feature"],
  "would_release": [],
  "removed": [
    {"kind": "postgres_database", "name": "wtenv_old_feature_aa11bb22", "worktree": "/code/old-feature"},
    {"kind": "port_block", "name": "20030-20039", "worktree": "/code/old-feature"},
    {"kind": "registry_entry", "name": "/code/app/.git/worktrees/old-feature", "worktree": "/code/old-feature"}
  ],
  "would_remove": [],
  "already_absent": [
    {"kind": "env_section", "name": "/code/old-feature/.env.local", "worktree": "/code/old-feature"}
  ],
  "failed": [],
  "kept": [
    {
      "path": "/Volumes/usb/wt",
      "git_dir": "/code/app/.git/worktrees/wt",
      "reason": "git_still_lists",
      "current_path": null
    }
  ],
  "skipped_busy": [],
  "no_entry": []
}
```
