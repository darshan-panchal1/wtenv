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
