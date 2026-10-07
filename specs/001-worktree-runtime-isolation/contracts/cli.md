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
| `env_file_unusable` | `path`; `reason`: `is_directory`, `parent_missing`, `not_writable`, `tracked_by_git`, `markers_damaged`, `symlink` (FR-086; `path` is the link), `missing` (`exec` only), or `no_section` (`exec` only) |
| `dependency_unavailable` | `dependency`: `git`, `docker`, or `postgres`; `reason`: `not_installed`, `too_old`, `not_running`, `not_local`, `cannot_connect`, `authentication_failed`, or `permission_denied`; `required` and `found` for `too_old` |
| `template_missing` | `kind` (`postgres` or `sqlite`); `template` |
| `template_in_use` | `kind`; `template`; `connections` (Postgres only) |
| `ownership_conflict` | `kind` (an `ItemKind`); `name` |
| `post_up_failed` | `command`; `exit_status` |
| `registry_busy`, `worktree_busy` | `waited_seconds` |
| `registry_unreadable` | `path`; `reason`: `invalid_json`, `invalid_schema`, `unknown_version`, `not_readable`, or `lock_unsupported` |
| `problems_found` | `problems` (count) |
| `worktree_exists` | `path`; `current_path` when the worktree was moved; `not_attempted` (`gc --release` stopped at a worktree that reappeared: the named paths it did not get to, in command-line order) |
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
| `unverifiable` | The worktree cannot be found as recorded, but its removal is not confirmed. `reason` is `repository_not_found`, `git_still_lists`, `moved`, `path_exists`, or `parent_missing` (the directory that holds the recorded path is missing, for example a drive that is not mounted, after git has pruned the worktree; reading R9) |

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
   not tracked by git, writable, markers intact. Otherwise `env_file_unusable`. Neither the
   path nor any directory between the worktree root and it may be a symbolic link;
   otherwise `env_file_unusable`, reason `symlink` (FR-086). The same rule applies to the
   override path in step 5, to `.wtenv/` and the SQLite copy in step 7, to a recorded
   env file or override that a configuration change makes `up` remove, and to the
   repository's `.git/info/exclude` (step 9; `path` is the link). A recorded env file or
   override that `up` would remove must also pass the checks of recorded values (`wtenv
   down`, "Recorded values"); otherwise `ownership_conflict`, with `details.kind` the item
   kind and `name` the recorded path, and nothing changes.
5. If compose is configured: check Docker and the Compose version, the compose file's name,
   that no override file of the developer's exists, and that `COMPOSE_PROJECT_NAME` and
   `COMPOSE_FILE` are not set. Resolve the compose file and count the ports it publishes.
   The override path must not be a symbolic link or sit under one (step 4).
   An override file that is recorded and exists must start with wtenv's header line
   (files.md, Compose override file); otherwise `ownership_conflict`, with `details.kind`
   `compose_override` and `name` the override path, and nothing changes (FR-087). A file
   that has the header and is only out of date passes, and step 11 rewrites it. A recorded
   override that is missing passes, and step 11 creates it again. The header is checked again
   in step 11, immediately before the override is rewritten or deleted (FR-087). A recorded compose
   project must pass the compose-project check of `wtenv down`, "Recorded values"; otherwise
   `ownership_conflict`, with `details.kind` `compose_project`, `name` the recorded
   project, and nothing changes (FR-088).
6. Check that the block holds all port variables and published ports. Otherwise
   `config_invalid` with `details.min_block_size` (FR-014, FR-032).
7. If a database is configured: check that the URL pattern resolves and names a local host.
   For SQLite, `.wtenv/` and the copy's path must not be symbolic links (step 4).
   A recorded Postgres database must pass the database check of `wtenv down`, "Recorded
   values" (name, `<id8>`, local host), and a recorded SQLite path must be exactly
   `.wtenv/<file name>`; otherwise `ownership_conflict`, with `details.kind`
   `postgres_database` or `sqlite_file`, `name` the recorded name or path, and nothing
   changes (FR-088). A SQLite copy recorded in state `removing` is not reused: `unsupported`,
   reason `interrupted_removal`, `name` the copy's path; run `wtenv down` first (FR-088).

   *From here on, `up` changes things, in this order:*
8. Registry: create the entry, or record a new location (FR-084); allocate a block when the
   entry has none or the block size changed (`no_free_block` if none is free); assign
   ports. Save the entry as `incomplete` with its block, ports, `exclude_patterns`, and the
   env-file record in state `creating`.
9. Exclude block: write the worktree's patterns to `.git/info/exclude`.
10. Database: create it from the template if it is not recorded. Failures here are
    `template_missing`, `template_in_use`, `ownership_conflict`, or `dependency_unavailable`.
11. Compose: write the override file and verify it.
12. Env file: write wtenv's section.
13. Run the post-up commands in order. The first non-zero exit stops `up` with
    `post_up_failed`.
14. Registry: mark the worktree `provisioned`.

A failure in steps 8 to 13 leaves what was created in place and recorded, with the worktree
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
`compose_fixed_volume_name`, `worktree_moved`.

`compose_fixed_volume_name` (reading R8): the resolved compose model has a top-level volume,
not `external`, whose `name` is not `<model name>_<volume key>`, so it was given a fixed
`name:`. `details` has `volume` (the key) and `name`. Every worktree and the main checkout
share such a volume, so `down` and `gc` never remove it (`wtenv down`).

**Exit statuses**: 0, 3, 4, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 19.

## `wtenv down`

Releases everything the registry records for the current worktree (FR-038).

| Option | Effect |
|--------|--------|
| `--dry-run` | Changes nothing, takes no worktree lock, and lists what `down` would remove (FR-040). An item that cannot be checked because Postgres or Docker cannot be reached is listed under `failed`, with a `reason` naming the dependency, not under `would_remove`; the exit status stays 0 |

**Order of work**: compose project (containers, networks, volumes, then the override file).
`docker compose down` is run without `--volumes`. Docker gives the project label
`com.docker.compose.project` only to named volumes; an anonymous volume and an external
volume carry none. So, before `docker compose down`, wtenv runs `docker inspect` on the
project's containers (found by the label) and collects the volumes they mount. Then:

- each volume labelled `com.docker.compose.project=<recorded project name>` whose name
  starts with `<recorded project name>_` is removed by name, as its own item in `removed`
  or, with `--dry-run`, `would_remove`;
- each volume with that label whose name does not start with `<recorded project name>_`
  was given a fixed `name:` in the compose file, and other worktrees and the main checkout
  share it (reading R8). It is never removed and never named in a removal command. It is
  reported in `kept_volumes` with `name`, `project`, and `reason` `fixed_name`, with or
  without `--dry-run`. `up` warns about such a volume (`compose_fixed_volume_name`);
- each mounted volume without that label, anonymous or external, is never removed and
  never named in a removal command. It is reported in `kept_volumes` with `name`, `project`
  (the recorded project name), and `reason` `unlabelled`, with or without `--dry-run`. The
  inspection runs for `--dry-run` too, and changes nothing. A project with no container
  left has nothing to inspect, so no unlabelled volume is reported for it.

**Known limits**: an anonymous volume of a removed project stays on disk, listed in
`kept_volumes`, because nothing but the container's mount ties it to the project. A volume
with a fixed name stays too, even when this worktree's project created it. Remove either
yourself with `docker volume rm NAME` once you are sure it holds nothing you need. An opt-in
cleanup of anonymous volumes is on the roadmap (docs/roadmap.md).

**Recorded values.** The registry is a file a person can edit, so `down` acts on a recorded
value only when it has the form wtenv records. Each check runs before anything is listed,
inspected, connected to, or deleted, in a dry run as in a real one. A value that fails is
an item under `failed` (the exit status is 13; with `--dry-run`, 0), its record stays, and
nothing is done for it:

- compose project: matches `wtenv-[a-z0-9-]{1,40}-[0-9a-f]{8}` in full, and its last 8
  digits are the `<id8>` of the entry's own git directory (files.md, Names). Otherwise
  nothing of the project is listed or removed, and the override file is kept with it;
- Postgres database: the name matches `wtenv_[a-z0-9_]{1,40}_[0-9a-f]{8}` in full and ends
  in `_<id8>` of the entry's own git directory; the host is `localhost`, `127.0.0.1`, or
  `::1`. Both are checked before connecting;
- env file, override file, and SQLite copy: the recorded path is relative, unchanged by
  normalisation, and has no `..` part. The SQLite copy is exactly `.wtenv/<file name>`. The
  override's file name is one of the four in files.md, Names, and the file starts with
  wtenv's header line (files.md, Compose override file) before it is deleted.

Volumes are not recorded: they are found by the label of the recorded project, so the
project check covers them.

`wtenv up` applies the same checks to the compose project, the Postgres database (name and
host), and the SQLite copy before it reuses them, and fails with `ownership_conflict` (exit 11)
and a `reason` naming the field when one fails (FR-088; `wtenv up`, steps 5 and 7).

After that: databases; wtenv's section of the env file (and the file itself when wtenv created it and
nothing else is in it); registry entry and port block; and, when this was the last
registered worktree of the repository, wtenv's entries in `.git/info/exclude` (FR-085).

- SQLite side files (FR-039): each existing `-wal`, `-shm`, or `-journal` file beside the
  recorded SQLite copy appears as its own item in `removed` (or in `would_remove` with
  `--dry-run`), with `kind` `sqlite_file` and `name` the file's absolute path, the same shape
  as the database file's own item. A side file that does not exist is not listed. Side files
  are removed only together with the recorded database file: when the recorded copy is
  already gone, it is reported under `already_absent` and its side files are left in place.
  A side file that is a symbolic link (SQLite never makes one) puts the copy and each of its
  side files under `failed` with `reason` `symlink`, and none of them is deleted.
- A file that cannot be changed or deleted (an `OSError`, such as a read-only directory)
  is an item under `failed`, with a `reason` naming the path and the system's error; it
  stays recorded, and the exit status is 13.
- Tracked env file (FR-018): when git tracks the recorded env file (the developer force-added
  it after `up`), `down` does not rewrite or delete it. The `env_section` item is reported
  under `failed` with `reason` `tracked_by_git`, stays recorded, and the exit status is 13.
  With `--dry-run`, it is listed under `failed` instead of `would_remove`, and the exit status
  stays 0.
- A worktree with no registry entry: success, nothing changed (FR-043).
- A recorded item that is already gone is reported under `already_absent`; that is not an
  error (FR-042).
- An item that cannot be removed stays recorded and is reported under `failed`. The entry
  stays, as `incomplete`, and the exit status is 13. Running `down` again finishes the job.
- Symbolic links (FR-086): when the recorded env file, the override file, `.wtenv/`, or the
  SQLite copy is a symbolic link, or any directory from the worktree root down to it is one
  (the root included, which only `gc --release` can meet), `down` does not follow or delete
  it. The item is reported under `failed` with `reason`
  `symlink`, stays recorded, and the exit status is 13, as for damaged markers. For the
  SQLite copy, its side files are neither listed nor touched. With `--dry-run`, the item is
  listed under `failed` instead of `would_remove`, and the exit status stays 0. A link in
  a directory above the worktree root, which only `gc` can meet because it uses the
  recorded path, puts every file item under `failed` with `reason` `symlink` in the same
  way. The same rule covers the repository's `.git/info/exclude`: when it is a link, the
  `exclude_entries` item is `failed` with `reason` `symlink`.
- `down` uses only the registry. A missing or invalid `wtenv.toml` does not stop it; an
  invalid one produces the warning `config_ignored`.
- The Postgres password for the drop comes from `wtenv.toml` when it resolves, otherwise from
  the libpq sources (`PGPASSWORD`, `~/.pgpass`).

**Warnings**: `config_ignored`, `worktree_moved` (FR-084: the worktree moved since the last
`up`; `down` records the new location before releasing).

**JSON**: `DownResult`. **Exit statuses**: 0, 4, 13, 14, 15, 16.

## `wtenv gc`

Releases registry entries whose worktrees git confirms are gone, across all repositories
(FR-045 to FR-047, FR-072 to FR-075).

| Option | Effect |
|--------|--------|
| `--dry-run` | Changes nothing and lists what would be removed. As for `down --dry-run`, an item that cannot be checked is listed under `failed`, and its entry is not listed under `would_release` |
| `--release PATH` | Release the entry whose recorded worktree path is `PATH`, even though it is unverifiable. May be given several times. With this option, `gc` acts **only** on the named entries; it does not sweep (FR-073) |

**Plain `gc`**

1. Read the registry and classify every entry.
2. For each `orphaned` entry: try its worktree lock without waiting. If it is held, skip the
   entry and report it under `skipped_busy`.
3. Holding the lock, classify the entry again. If it is no longer `orphaned`, skip it
   (FR-074).
4. Release it as `down` would. This includes the SQLite side-file items, which are listed
   under `removed` and, with `--dry-run`, under `would_remove`, as for `down`. It also
   includes the symbolic-link rule of `down` (FR-086): such an item goes under `failed`
   with `reason` `symlink`, the entry stays recorded and is not listed under `released`,
   and the exit status is 13. Volumes of the entry's compose project are handled as for
   `down`: a volume without the project label, or with a fixed name, is never removed and
   is reported in `kept_volumes` with `reason` `unlabelled` or `fixed_name`, with or
   without `--dry-run`. The recorded-value checks of `down` apply too.
   After the compose step and before the first file is touched, `points_to` is read again
   on the recorded path. If it now names a git directory that exists, a worktree has
   appeared there, and every file item of the entry is `failed` with `reason`
   `worktree_exists`; the entry stays.
5. When the run releases the last entries of a repository, the last one also removes the
   `exclude_entries` block. A dry run lists that item for the same entry, because it counts
   the entries it would release as gone.

If anything else stops the release of an entry with an error (for example
`registry_busy`), `gc` stops there and prints its result as it stands: the entries and
items already released are reported, and `error` holds the error, with its exit status.

Entries that are `unverifiable` are reported under `kept` with their reason. Entries of
existing worktrees are not touched and not listed. Neither `kept` nor `skipped_busy` changes
the exit status.

**`gc --release PATH`**

1. Every `PATH` is checked first. If any names an entry whose worktree still exists, at that
   path or (reason `moved`) at another, the command fails with `worktree_exists` and changes
   nothing. A directory whose `.git` file points to a git directory that no longer exists
   is not a worktree, so its entry can be released.
   A directory whose `.git` names a git directory that exists is a worktree, even when that
   git directory is not the one the entry records, so such a `PATH` fails too.
   `PATH` matches the entry recorded at `PATH` made absolute, or else the one recorded at
   its resolved path, so an entry whose recorded path is now a symbolic link is still found;
   its files are then left alone under the symbolic-link rule of `down` (FR-086).
   An entry whose recorded git directory still exists is refused with `worktree_exists`
   too: git still has that worktree, perhaps moved by hand or under a renamed directory.
   The hint says to run `git worktree repair` from the new location, or
   `git worktree prune`, first.
2. A `PATH` with no entry is reported under `no_entry`. That is not an error, so the command
   can be repeated safely.
3. Each remaining entry is released as `down` would, with the same lock rule as plain `gc`.
   Holding the lock, immediately before each delete, the step-1 check is repeated. If the
   worktree has reappeared, that entry is stopped with `worktree_exists` (exit 18) and
   nothing of it is changed. The entries released before it stay in the result. The named
   paths after it are not attempted; they are listed in `error.details.not_attempted` and
   in the message. When an earlier entry has items under `failed`, the hint says so, since
   exit 18 hides the partial failure.

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
- The variables are those in wtenv's section of the recorded env file (`EnvFileRecord.path`),
  ports included, unquoted as in [files.md](files.md#env-file-section). `wtenv.toml` is not
  read. The developer's own lines in the env file are not loaded.
- The env file missing, present with no wtenv section (neither marker line), or its markers
  damaged: `env_file_unusable` (reason `missing`, `no_section`, or `markers_damaged`),
  reported with exit status 125; the command does not run.
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
