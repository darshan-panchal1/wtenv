# Data model: wtenv — Per-Worktree Runtime Isolation

**Feature**: `001-worktree-runtime-isolation` | **Date**: 2026-10-03 | **Plan**: [plan.md](plan.md)

This document defines what wtenv stores and how it derives everything else from it. The
registry is the only state wtenv keeps (FR-066, Principle III). Formats that other tools read
are in [contracts/files.md](contracts/files.md); `wtenv.toml` is in
[contracts/config.md](contracts/config.md).

All models are pydantic v2 models with `extra="forbid"`, or plain dataclasses where nothing
is validated or serialised (Principle IX).

---

## Entities

### Worktree identity

Computed on every command that acts on the current worktree; never stored on its own.

| Field | Source | Notes |
|-------|--------|-------|
| `git_dir` | line 1 of `git rev-parse --path-format=absolute --absolute-git-dir --show-toplevel --git-common-dir`, passed through `os.path.realpath` | **The identity** and the registry key (FR-006) |
| `path` | line 2, `realpath` | The worktree's location now |
| `repository` | line 3, `realpath` | The common git directory; the same for all worktrees of a repository |

Git is run with git's repository-local environment variables removed, so an inherited
`GIT_DIR` cannot redirect it. Exit status 128 means "not inside a worktree"
(`not_in_worktree`).

### Registry file

`<state>/registry.json` ([files.md](contracts/files.md#state-directory)). Read and written
only while holding the registry lock; written through a temporary file, `fsync`, and
`os.replace` (Principle III). A file that is not valid JSON, does not match the schema, or
has an unknown `version` stops every command with `registry_unreadable`; wtenv never
rewrites such a file (FR-070).

```json
{
  "version": 1,
  "worktrees": {
    "/code/app/.git/worktrees/feature-x": {
      "git_dir": "/code/app/.git/worktrees/feature-x",
      "path": "/code/feature-x",
      "repository": "/code/app/.git",
      "state": "provisioned",
      "block": {"start": 20010, "size": 10},
      "ports": [
        {"variable": "PORT", "port": 20010},
        {"variable": "DB_PORT", "port": 20011}
      ],
      "published": [
        {"service": "cache", "target": 6379, "protocol": "tcp", "host_ip": null, "port": 20012, "variable": null},
        {"service": "db", "target": 5432, "protocol": "tcp", "host_ip": null, "port": 20011, "variable": "DB_PORT"}
      ],
      "env_file": {"path": ".env.local", "created_file": true, "added_newline": false, "state": "created"},
      "databases": [
        {"kind": "postgres", "name": "wtenv_feature_x_3f9a1c2b", "host": "localhost", "port": 5432, "user": "myapp", "path": null, "state": "created"}
      ],
      "compose": {
        "project": "wtenv-feature-x-3f9a1c2b",
        "file": "compose.yaml",
        "override": "compose.override.yaml",
        "override_state": "created"
      },
      "exclude_patterns": ["/.env.local", "/compose.override.yaml"]
    }
  },
  "hooks": {
    "/code/app/.git": {"hook_file": "/code/app/.git/hooks/post-checkout", "created_file": true}
  }
}
```

### Registry entry (`WorktreeEntry`)

One per provisioned worktree, keyed by `git_dir`.

| Field | Type | Rules |
|-------|------|-------|
| `git_dir` | absolute path | Equals the key |
| `path` | absolute path | Location at the last `up` or `down` in the worktree. Updated when the worktree moved (FR-084) |
| `repository` | absolute path | Common git directory |
| `state` | `"incomplete"` or `"provisioned"` | See [Entry states](#entry-states) |
| `block` | `PortBlock` | Always present once the entry exists |
| `ports` | list of `VariablePort` | Same order as `ports` in `wtenv.toml` |
| `published` | list of `PublishedPort` | Empty without compose |
| `env_file` | `EnvFileRecord` or null | |
| `databases` | list of `DatabaseRecord` | At most one per kind. A kind no longer configured stays here until `down` (FR-065) |
| `compose` | `ComposeRecord` or null | |
| `exclude_patterns` | list of strings | This worktree's generated paths, as written to `.git/info/exclude` |

Credentials are never stored (FR-019). A Postgres user name is stored because teardown needs
it to connect; the password never is.

### Port block (`PortBlock`)

| Field | Rules |
|-------|-------|
| `start` | `20000 + k × size` for some whole number `k` |
| `size` | 1 to 1000; `start + size - 1 ≤ 29999` |

No two blocks in the registry share a port (FR-008). A block changes only when `block_size`
changes, or when the entry is released (FR-011).

### Variable port and published port

| Model | Fields |
|-------|--------|
| `VariablePort` | `variable`, `port` |
| `PublishedPort` | `service`, `target` (container port), `protocol` (`tcp` or `udp`), `host_ip` (or null), `port` (the assigned host port), `variable` (the variable it is tied to, or null) |

### Env file record (`EnvFileRecord`)

| Field | Rules |
|-------|-------|
| `path` | Relative to the worktree root |
| `created_file` | True when wtenv created the file. Then `down` deletes the file if nothing else is left in it (FR-038) |
| `added_newline` | True when wtenv added a line break before its section; `down` removes it again (FR-079) |
| `state` | A [resource state](#resource-states) |

### Database record (`DatabaseRecord`)

| Field | Postgres | SQLite |
|-------|----------|--------|
| `kind` | `"postgres"` | `"sqlite"` |
| `name` | Database name ([files.md](contracts/files.md#names)) | null |
| `host`, `port`, `user` | From the URL pattern at creation | null |
| `path` | null | Copy's path, relative to the worktree root |
| `state` | A resource state | A resource state |

### Compose record (`ComposeRecord`)

| Field | Rules |
|-------|-------|
| `project` | Compose project name ([files.md](contracts/files.md#names)); recorded before anything else is done for compose, and never changed |
| `file` | Compose file, relative to the worktree root |
| `override` | Generated override file, relative to the worktree root |
| `override_state` | A resource state for the override file |

The project's containers, networks, and volumes are not listed in the registry: Docker
labels them with the project name, and every resource with the label
`com.docker.compose.project=<project>` belongs to the recorded project (constitution v1.0.1).

### Hook record (`HookRecord`)

Keyed by repository. `hook_file` (absolute path) and `created_file` (true when wtenv created
the file). Lets `hook uninstall` delete a file only if wtenv created it.

### Configuration (`Config`)

Parsed from `wtenv.toml` with `tomllib` and validated by pydantic; the settings and rules are
in [contracts/config.md](contracts/config.md). Defaults: `ports = ["PORT"]`,
`block_size = 10`, `env_file = ".env.local"`, `post_up = []`, no `database`, no `compose`.

---

## Entry states

| From | Event | To |
|------|-------|----|
| no entry | `up` starts | `incomplete` |
| `incomplete` | `up` finishes, post-up commands included | `provisioned` |
| `provisioned` | `up` finds nothing to change and the post-up commands pass | `provisioned` |
| `provisioned` | `up` has to change a resource, a post-up command fails, or `down` starts | `incomplete` |
| `incomplete` | `down` or `gc` removes only part of what is recorded | `incomplete` |
| either | `down` or `gc` removes everything | entry deleted |

- `incomplete`: an `up` or `down` was interrupted or partly failed (FR-049).
- `provisioned`: the last `up` completed, including its post-up commands. Only then does
  `exec` run commands (FR-057).
- A repeat `up` that changes nothing stays `provisioned` throughout, so an `exec` running at
  the same time is not refused.

## Resource states

Every recorded resource carries a state. This is how wtenv meets FR-067 (nothing created is
ever unrecorded) and FR-069 (recovery without manual repair).

| State | Meaning | Next `up` | `down` and `gc` |
|-------|---------|-----------|-----------------|
| `creating` | Recorded **before** creating. It may or may not exist. | If it exists, keep it and mark `created`; otherwise create it | Remove it if it exists |
| `created` | Exists, as far as wtenv knows | Keep it. If it has gone missing, create it again and report that | Remove it |
| `removing` | Recorded **before** removing. It may be gone, or half gone. | If it is still there and usable, keep it and mark `created`. If it is gone, create it again. If it is half removed (a Postgres database the server marks invalid), fail with `unsupported`, reason `interrupted_removal`, and ask for `wtenv down` | Remove it |

Before recording `creating`, wtenv checks, under the worktree lock, that nothing exists at
the target name. Something found then belongs to someone else and is an
`ownership_conflict` (FR-024). After an interruption, something found at a name recorded as
`creating` was created by wtenv's interrupted run.

`up` never removes a database, a container, or a volume, whatever the state. The only
things `up` deletes are files it generated and can generate again: its own env section when
`env_file` changed, and its own override file when the compose file moved or `[compose]` was
removed. Removing data is the job of `down` and `gc`, which have `--dry-run` (Principle II).

Reading of Principle II's "destructive command": regenerating or removing a file that wtenv
itself generated and recorded, such as the old env section or a moved override, is part of
`up` and `down` and is reported in their results. It does not make `up` a destructive
command that needs `--dry-run`.

---

## Status and the orphan checks

`ls`, `doctor`, `exec`, and `gc` use one function, `classify(entry, listing)`, so they can
never disagree. `listing` is the output of
`git --git-dir=<repository> worktree list --porcelain`, run once per repository, or nothing
when that fails.

`points_to(p)` reads `p/.git`: a directory is its own git directory; a file holds
`gitdir: <path>`, resolved against `p`. Missing or unreadable gives nothing.

1. `listing` is nothing (repository gone or not a repository): **unverifiable**,
   `repository_not_found`.
2. `points_to(entry.path)` is `entry.git_dir`: the worktree exists as recorded. **provisioned**
   if `entry.state` is `provisioned`, otherwise **incomplete**.
3. `entry.git_dir` is still a directory, so git still has this worktree:
   1. `entry.path` is missing and `listing` still lists it: **unverifiable**,
      `git_still_lists` (unmounted drive, or deleted by hand).
   2. Otherwise: **unverifiable**, `moved`. `current_path` is the listed path whose
      `points_to` is `entry.git_dir`, if any.
4. `entry.git_dir` is gone:
   1. Something exists at `entry.path`: **unverifiable**, `path_exists`.
   2. `listing` lists `entry.path` (another record at that path): **unverifiable**,
      `git_still_lists`.
   3. Otherwise: **orphaned**. All three checks of FR-045 hold: the repository answers, git
      has neither this worktree nor anything at its path, and nothing exists at the path.

Worktrees of the current repository that appear in `listing`, exist on disk, and whose
`points_to` is not a registry key are shown as **unprovisioned** by `ls`.

`gc` runs `classify` again, with a fresh `listing`, while holding the entry's worktree lock,
immediately before releasing it (FR-074).

---

## Port allocation

**Range** `20000–29999`. **Block search**, done inside one registry transaction:

1. `k = 0, 1, 2, …` while `20000 + k × size + size - 1 ≤ 29999`.
2. Skip the candidate if it overlaps any block in the registry, across all repositories
   (FR-008).
3. Skip it if any of its ports fails the free-port test: a TCP `bind` without
   `SO_REUSEADDR` on `127.0.0.1`, `0.0.0.0`, and `::1`; an unavailable address family is
   skipped (FR-009).
4. Take the first candidate left. None left: `no_free_block`, and nothing is saved (FR-012).

The same registry and the same free ports always give the same block. Because the search and
the write share one transaction, simultaneous `up` runs in different worktrees get different
blocks (FR-013, SC-005).

**Assigning ports within the block** is recomputed from the configuration on every `up`, so
the same configuration always gives the same result (FR-017, FR-065):

1. Variable `i` (counting from 0) in `ports` gets `start + i`.
2. Compose (when configured): the compose file is resolved once by Compose, with all
   profiles (`--profile "*"`) and with variable `i` set to the marker value `i + 1`. A
   published port that resolves to marker `i + 1` is **tied** to variable `i` and gets that
   variable's port (FR-031). Markers are used instead of real ports so that the analysis
   needs no block and cannot confuse a hard-coded port with a variable's port.
3. Every other published port, including one with no host port, gets the next port after
   the variables, in the order (service, container port, protocol, host IP).
4. A published host range: `unsupported` (`compose_port_range`, FR-033). Two mappings with
   the same protocol and host IP that end up on the same port: `unsupported`
   (`compose_port_clash`).
5. More ports than the block holds: `config_invalid` with
   `min_block_size = len(ports) + untied published ports` (FR-014, FR-032).

Steps 2 to 5 run before anything is changed, because the marker values need no block.

---

## Locks

| Lock | Scope | Bound | Taken by |
|------|-------|-------|----------|
| Registry lock | All of the user's registry | 10 s, then `registry_busy` | Every command, for one read or one read-check-write |
| Worktree lock | One `git_dir` | 60 s, then `worktree_busy`; `gc` does not wait | `up` and `down` for their whole run; `gc` per entry |

A command never waits for a worktree lock while it holds the registry lock, and never holds
the registry lock while copying a database, calling Docker, or running a post-up command
(FR-068). Read-only work (`ls`, `doctor`, `exec`, `--dry-run`) takes only the registry lock,
briefly (FR-076). Both are `flock` locks, so a killed process releases them (FR-078).

---

## Relationships

```text
Registry 1 ── * WorktreeEntry (key: git_dir)
WorktreeEntry 1 ── 1 PortBlock
WorktreeEntry 1 ── * VariablePort, * PublishedPort   (all inside the block)
WorktreeEntry 1 ── 0..1 EnvFileRecord
WorktreeEntry 1 ── 0..2 DatabaseRecord               (at most one per kind)
WorktreeEntry 1 ── 0..1 ComposeRecord ── Docker resources labelled with its project
Repository   1 ── * WorktreeEntry                    (shared .git/info/exclude block)
Registry 1 ── * HookRecord (key: repository)
```
