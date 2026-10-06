# wtenv roadmap

Ideas that are **not** in a spec. Constitution Principle X: anything not in the spec is
recorded here instead of being built. An item leaves this file only by entering a spec.

Each item names where it came from, so the reasoning can be found again.

## From planning feature 001 (2026-10-03)

Source for all of these: `specs/001-worktree-runtime-isolation/research.md`.

### Claude Code

| Idea | Why it is not in v1 |
|------|---------------------|
| **`SessionStart` hook that runs `wtenv up`.** An opt-in install action adds a command hook to the main checkout's `.claude/settings.local.json`. Observed to fire with its working directory set to the new worktree. Extended on 2026-10-05 (see "Chosen for feature 002" below): the hook also returns an agent brief from `wtenv context`. | It is not the worktree lifecycle hook FR-055 describes, gives no `down`, and does not cover subagent worktrees or `EnterWorktree` in the middle of a session (research section 2). |
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

## Chosen for feature 002 (2026-10-05)

Source: a planning session with the maintainer on 2026-10-05. These ideas were picked as the
next feature, to be specified after v1 is tagged. They stay here until feature 002's spec
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
| **Keep the source distribution small.** `uv build` puts `.claude/`, `.specify/`, `specs/`, and `tests/` in the sdist, because hatchling includes every file git does not ignore. A `[tool.hatch.build.targets.sdist]` `include` list would limit it to the source, the license, the README, the changelog, and `pyproject.toml`. The wheel holds only `wtenv/` and its metadata. | Packaging beyond T144 and T145 (Principle X). Nothing breaks: the sdist builds a correct wheel. |
| **Keep the pins in CI current.** The actions in `.github/workflows/` are pinned to full commit SHAs, and the `minimums` job pins git 2.31.0 and Compose 2.24.4 by SHA-256. Dependabot (`github-actions` ecosystem) would propose new action SHAs. The two downloads would still be updated by hand. | A new file and a policy decision outside T141. The pins are what make the workflows reproducible, so they change only on purpose. |
| **Run the suite against Compose 2.24.4 on the machine that supports it.** The `minimums` job runs on x86-64 Linux only; wtenv's own Docker tests have not been run on an Arm host with Compose 2.24.4. | No such runner is in the CI plan (plan.md, Build, CI and release). |
