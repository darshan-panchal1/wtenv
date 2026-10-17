# wtenv roadmap

Ideas that are **not** in a spec. Constitution Principle X: anything not in the spec is
recorded here instead of being built. An item leaves this file only by entering a spec.

Each item names where it came from, so the reasoning can be found again.

## From planning feature 001 (2026-10-03)

Source for all of these: `specs/001-worktree-runtime-isolation/research.md`.

### Claude Code

| Idea | Why it is not in v1 |
|------|---------------------|
| **`SessionStart` hook that runs `wtenv up`.** An opt-in install action adds a command hook to the main checkout's `.claude/settings.local.json`. Observed to fire with its working directory set to the new worktree. Extended on 2026-10-05 (see "Chosen for feature 003" below): the hook also returns an agent brief from `wtenv context`. | It is not the worktree lifecycle hook FR-055 describes, gives no `down`, and does not cover subagent worktrees or `EnterWorktree` in the middle of a session (research section 2). |
| **Replacement hooks.** wtenv's `WorktreeCreate` hook runs `git worktree add` and `wtenv up`; its `WorktreeRemove` hook runs `wtenv down` and `git worktree remove`. This is the only way to get an automatic `down`. | It replaces Claude Code's own worktree handling: `.worktreeinclude`, base-branch choice, pull-request worktrees, exit prompts, branch deletion, and the cleanup sweep are lost, and wtenv would delete worktree directories (research section 2). |
| **Automatic `gc`** from a session hook, to reclaim worktrees Claude Code removed. | FR-051 limits the hook to `up`. Safe in principle, because `gc` only releases what git confirms is gone. |

### Compose

| Idea | Why it is not in v1 |
|------|---------------------|
| **Compose files with other names, several compose files, or a developer's own override file**, by writing `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME` into an env file. | Compose reads only `.env`, and wtenv's default env file is `.env.local`, so FR-030 ("no extra flags") would not hold by default (research section 4). |
| **Remapping a host port range onto one container port** (`"8000-9000:80"`). | Compose chooses the port at start; it cannot be pinned (FR-033). |
| **A setting that ties a service port to a variable** without editing the compose file. | FR-063's settings list is closed. v1 ties them through `${VAR}` in the compose file. |

### Databases

| Idea | Why it is not in v1 |
|------|---------------------|
| **A `strategy` setting** for `CREATE DATABASE` (`FILE_COPY`, and on PostgreSQL 18 `file_copy_method = clone`), for multi-gigabyte templates. | FR-063's settings list is closed; the server default measured fast enough up to 381 MB (research section 3). |
| **Unix-socket Postgres URLs.** | Keeps the "local only" check (FR-025) to a host name comparison. |
| **A setting for the name of the database URL variable.** FR-020 calls `DATABASE_URL` the default, but FR-063 lists no setting for it. | FR-063's settings list is closed. |
| **Copying a busy template by dump and restore.** | Rejected in clarification: slower, needs client programs, second copy path. |

### Identity and state

| Idea | Why it is not in v1 |
|------|---------------------|
| **A token in the git directory** that tells a re-created worktree from the one it replaced, so the new one gets a fresh database instead of taking over the old entry. | New state outside the registry; the takeover is documented as an edge case (research section 6). |
| **Removing unused worktree lock files.** | Deleting a lock file that another process is waiting on breaks the lock (research section 9). They are empty files. |

### Other

| Idea | Why it is not in v1 |
|------|---------------------|
| **Testing UDP ports** when allocating a block. | The spec speaks of ports in use without naming a protocol; v1 tests TCP. |
| **An `--interactive` mode.** | v1 has no prompts at all (spec Assumptions). |
| **Replacing `filelock` with a small `fcntl.flock` helper** to save about 60 ms of import time. | Only if the NFR-001 benchmark fails; it changes the dependency list, so it needs the maintainer's approval (research section 8). |

## Chosen for feature 003 (2026-10-05)

Source: a planning session with the maintainer on 2026-10-05. These ideas were picked as the
next feature, to be specified after v1 is tagged. They stay here until feature 003's spec
takes them in.

| Idea | Why it is not in v1 |
|------|---------------------|
| **Agent brief: `wtenv context [--json]`.** A read-only command that tells an agent which worktree it is in, its port variables and block, its database name, and the blocks other worktrees hold. Delivered to Claude Code sessions through the `SessionStart` hook above. | Not in spec 001 (Principle X). The hook's output format for adding context still has to be checked against Claude Code's documentation. |
| **Database checkpoints: `wtenv db snapshot NAME`, `wtenv db restore NAME`, `wtenv db snapshots`.** A snapshot is a copy of the worktree's own database (`CREATE DATABASE … TEMPLATE`, or a file copy for SQLite), recorded in the registry so `down` and `gc` remove it with the worktree (Principle II). `restore` replaces the worktree's database with the snapshot and supports `--dry-run`. | Not in spec 001 (Principle X). Postgres cannot copy a database that has open connections, so the app server has to be stopped first; v1's `template_in_use` error already reports this. |
| **`up --db-from WORKTREE`**: start a worktree from another provisioned worktree's database instead of the template. | Not in spec 001 (Principle X). Same open-connection limit as checkpoints. |

## From the review of the destructive paths (2026-10-05)

Source: the read-only review of everything `down` and `gc` can delete, run on 2026-10-05.
Its findings that could remove something unrecorded or hide an error became tasks
(`specs/001-worktree-runtime-isolation/tasks.md`, T151–T171); this one did not.

| Idea | Why it is not in v1 |
|------|---------------------|
| **`gc --dry-run` showing which entries a real run would skip as busy** (review LOW-3). Today a dry run lists an entry under `would_release` even when its worktree lock is held, and the real run then reports it under `skipped_busy`. At least, say so in cli.md. | A dry run may not take a worktree lock (FR-076). The difference only ever makes the real run release less than the dry run listed, never more, and `skipped_busy` is not a failure (FR-077). |

## From reading R6 (2026-10-05)

Source: the probe behind the R6 amendment (`specs/001-worktree-runtime-isolation/tasks.md`,
Readings table; T162, T163).

| Idea | Why it is not in v1 |
|------|---------------------|
| **Opt-in cleanup of anonymous volumes of removed projects** (R6, 2026-10-05), for example `down --anonymous-volumes`. Anonymous volumes carry no compose label (probe on Docker 29.5.3, Compose 5.1.4), so `down` and `gc` keep them and list them in `kept_volumes`. The option would remove those that only the removed project's containers mounted, and only with `--dry-run` available. | Nothing but the container's mount ties an anonymous volume to a project, and a volume can hold data the developer wants (Principle II), so v1 never removes one. |

## From review 2 of the destructive paths (2026-10-06)

Source: the second read-only review of everything `down` and `gc` can delete, after
T158–T173. Its findings became readings R8 and R9 and tasks T174–T211
(`specs/001-worktree-runtime-isolation/tasks.md`, group 9S); this idea did not.

| Idea | Why it is not in v1 |
|------|---------------------|
| **Opt-in removal of a fixed-name volume that this worktree's project created** (R8). `down` and `gc` keep every labelled volume whose name does not start with `<project>_` and list it in `kept_volumes` with reason `fixed_name`. An option could remove one when no other project's container mounts it, with `--dry-run` available. | A volume with a fixed `name:` is shared by the main checkout and every worktree, and its label only says which project created it first (Principle II). |

## From review 3 of the destructive paths (2026-10-06)

Source: the third read-only review, after group 9S and T212/T213. Its MEDIUM finding and three
of its LOW findings became tasks T214–T221
(`specs/001-worktree-runtime-isolation/tasks.md`, group 9S, "Found after the close"). The rest
did not.

| Idea | Why it is not in v1 |
|------|---------------------|
| **Remaining LOW findings of review 3**: (1) recorded `exclude_patterns` are added to the exclude block unchecked, and a hand-edited pattern can hide untracked files or raise `ValueError`; (2) `_finish` trusts the recorded `repository` for the exclude path; (3) `check_sqlite_target` then `os.replace` leaves a millisecond race (use `os.link`); (4) lines a developer writes between the markers are dropped without a warning (`env_section_edited`); (5) `gc` treats a worktree whose parent directory is an unmounted mount point or was renamed by hand as an orphan once git has pruned it (treat an empty parent as `parent_missing`, document `git worktree lock`); (6) the `<project>_` prefix rule covers volumes but not networks with a fixed `name:`; (7) `down` and `gc` do not check that the Docker endpoint is local; (8) `gc --dry-run` takes no worktree lock, so it can list an entry that a real run reports as `skipped_busy`, and run-time failures show as `would_remove`. | Each needs a hand-edited registry, a race of milliseconds, or an unusual setup, and none removes a resource wtenv did not create without that. Items 4 and 8 follow from documented behaviour (FR-076). Add the ones the maintainer picks as task pairs. |

## From the release work (2026-10-06)

Source: T141, T144, and T145, while writing CI and checking `uv build`.

| Idea | Why it is not in v1 |
|------|---------------------|
| **Keep the pins in CI current.** The actions in `.github/workflows/` are pinned to full commit SHAs, and the `minimums` job pins git 2.31.0 and Compose 2.24.4 by SHA-256. Dependabot (`github-actions` ecosystem) would propose new action SHAs. The two downloads would still be updated by hand. | A new file and a policy decision outside T141. The pins are what make the workflows reproducible, so they change only on purpose. |
| **Run the suite against Compose 2.24.4 on the machine that supports it.** The `minimums` job runs on x86-64 Linux only; wtenv's own Docker tests have not been run on an Arm host with Compose 2.24.4. | No such runner is in the CI plan (plan.md, Build, CI and release). |

## From the release work, after v0.1.0

Source: a read of the code after v0.1.0 was published.

| Idea | Why it is not in v1 |
|------|---------------------|
| **Exit-code-only checks.** Places that treat exit status 0 as success without checking the output: `_list_project` (`compose.py`, ~719), `_unlabelled_mounts` (`compose.py`, ~749), `_container_ports` (`doctor.py`, ~299), `git_status` (`doctor.py`, ~392), `git_listing` (`gitutil.py`, ~160), `git_path` (`gitutil.py`, ~168), and the `docker` fixture (`tests/conftest.py`, ~131). Each could check that the output has the expected shape, as the `docker info` check already does for the server version. | Not in spec 001 (Principle X). Each needs a task pair of its own, and the output of a wrong-but-successful command has to be probed first. |

## From the fastapi-template dogfood run

Source: running v0.1.0 on `fastapi/full-stack-fastapi-template` (2026-10-08). The v0.1.1 fixes
(T231 to T236) cover only what was wrong in what v0.1.0 already promised; everything here is new
behavior and needs a spec first (Principle X).

| Idea | Why it is not in v0.1.1 |
|------|-------------------------|
| **Delivered in 0.2.0 (spec 002).** **Repo-owned override file support. BLOCKER for repos like full-stack-fastapi-template**, which keep their own compose override. wtenv would work with the file the repository owns instead of refusing it. See also "Compose files with other names, several compose files, or a developer's own override file" above. | `up` refuses any override file wtenv did not write (`ownership_conflict`), because Compose loads only one and wtenv never edits a developer's (research section 4). Supporting one changes how the override is generated, and with it FR-030 and files.md. For now the README tells the developer how to merge it by hand. |
| **Delivered in 0.2.0 (spec 002); the variable names are `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>`, for example `WTENV_PORT_BACKEND_8000_TCP`, not the `WTENV_BACKEND_8000` example.** **Export the remapped published ports as variables**, for example `WTENV_BACKEND_8000` for the host port given to the `backend` service's container port 8000, so that other settings can refer to the port a service was given. | FR-031 ties a port to a variable through `${VAR}` in the compose file, and FR-063's list of settings is closed. New variables are a change to the env-file contract. |
| **Delivered in 0.2.0 (spec 002), as `type = "compose"` with `service`, `{port}` and `{compose_env:NAME}`.** **A way to point `[database]` at the compose db service**, instead of a Postgres server on the host or a SQLite file. | `[database]` names a local Postgres server and a template, or a SQLite template (FR-063). A database inside the worktree's own compose project is a different provisioning path. |
| **`wtenv up --dry-run`**, which shows what `up` would create and change, as `down` and `gc` already do. | Not in spec 001. `--dry-run` exists for the commands that remove things. |
| **`doctor` validates `wtenv.toml`, existing override files, and the Compose version.** It would report an invalid setting, an override file that would make `up` fail, and a Compose older than 2.24.4, before `up` is run. | FR-060 lists the checks of `doctor`, and none of these is in it. `up` already finds each of them, and fails before changing anything. |
| **`doctor` shows the Docker version** (the engine and the Compose plugin) next to the dependency status. | The `dependencies` list holds a status per dependency (cli.md, `wtenv doctor`); a version is a change to the `DoctorResult` model. |
| **Warn about a fixed `image:` name that worktrees share**, like `compose_fixed_volume_name`. A service with a fixed `image:` (usually with `build:`) gets the same image name in every worktree, so one worktree's build replaces the tag another one runs. | A new warning code is a change to the JSON models and to cli.md. |
| **`down` warns when a compose client for the released project is still running**, such as `docker compose watch`, which re-creates the containers after `down`. | wtenv records no process and has no way to find a client. Finding one needs a probe of its own. The README says to stop such clients first. |

## From planning feature 002 (2026-10-10)

Source: `specs/002-compose-dev-setup/plan.md`, "Roadmap entries", and the research it cites.
Feature 002 shipped in 0.2.0 (spec 002).

| Idea | Why it is not in 0.2.0 |
|------|------------------------|
| **A `--project-directory` equivalent**, so that Compose 2.24.4 reads the repository's `.env` from any subdirectory (research R2). | Compose 2.24.4 reads `.env` only from the current directory. Setting it would add flags or variables wtenv does not set today; the README says to run Compose from the compose file's directory. |
| **Showing a compose-hosted database in `ls`.** It is not recorded, so `ls` shows only its port and variable. | Nothing is recorded for it (FR-022, FR-023), and the `DATABASE` column stays `-`. |
| **Inspecting the image's `VOLUME` and `PGDATA`**, to narrow FR-026 and FR-027 to the data directory (research R4). | It needs `docker image inspect` on an image that may not be pulled, and `up` does not pull. Today every writable mount of the database service counts as data; the README says to use `:ro`. |
| **Naming two host addresses of one container port apart** (research R5). | The port variable name has no host address in it, so `up` refuses the setup with `port_variable_conflict`. |

## From the work on feature 002 (2026-10-17)

Source: the implementation of feature 002 (`specs/002-compose-dev-setup/tasks.md`, groups 4G, 4H
and 5B).

| Idea | Why it is not in 0.2.0 |
|------|------------------------|
| **`wtenv ls` text shows the compose arrangement** (`exec_only` or `automatic`). Only the JSON of `ls` and `up` carries it. | cli.md specifies the two JSON fields and the `up` text only; the table of `ls` gets no new column. |
| **Refuse a relative `driver_opts.device` for every volume driver type.** `database_storage` refuses it for `nfs`, `cifs` and a local bind (`type: none`), the three the data-model table lists; a relative device of another driver type is ignored. | The table lists three types, and a device of another type is not a path on the host. |

## From the spec 002 safety review (2026-10-10)

Source: `specs/002-compose-dev-setup/tasks.md`, "Deferred". These are the LOW findings of the
safety review that touch no write or delete path, the gaps found while building feature 002, and
the limits of `down` and `gc` found in group 4H. None is built.

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
- **`gc --release` cannot see that a file is tracked** (found in group 4H). The path is no longer
  a git worktree, so `is_tracked` cannot run. A header-less file at the recorded path is left
  alone and reported under `failed` (exit 13), but a git-tracked file that still carries wtenv's
  header is removed, as in v0.1.1. Failing closed for every override file when git cannot be
  asked would also stop `gc --release` cleaning up wtenv's own override of a worktree whose
  repository is gone; decide before building it.
- **`down`/`gc` text output does not list `kept_items`; JSON does** (found in group 4H). cli.md
  specifies the new list for JSON only, so a tracked override that `down` keeps is invisible in
  the text. Add a `kept` line per item, with its reason, to `render_down_text` and
  `render_gc_text`.
- **`compose_fixed_volume_name` judged under the wtenv project** (found while fixing safety H1).
  The v0.1.x warning still compares a volume's `name` with the repository's own model name, so
  `name: app_pgdata` in a repository named `app` is not warned about for services other than a
  compose-hosted database. Resolving with `-p <project>` in every mode would fix it but changes
  v0.1.1's resolution command (plan.md, Decisions to review 7).
