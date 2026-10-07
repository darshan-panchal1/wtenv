# Research: wtenv — Per-Worktree Runtime Isolation

**Feature**: `001-worktree-runtime-isolation` | **Date**: 2026-10-03 | **Plan**: [plan.md](plan.md)

Every section gives a **Decision**, the **Rationale**, the **Alternatives considered**, and
the **Evidence**. Evidence is of two kinds, and each item says which it is:

- **Documented**: a quotation from an official source, with its URL. All URLs were fetched on
  2026-10-03.
- **Observed**: the result of a command run on the machine below on 2026-10-03. Observed
  behaviour that is not documented is marked as such, and the plan does not depend on it
  without a test.

| Tool | Version used for the observations |
|------|-----------------------------------|
| OS | macOS 27.0, arm64 |
| git | 2.54.0 |
| Docker Engine / Compose | 29.5.3 (Docker Desktop) / 5.1.4 |
| PostgreSQL | 17.11 (official `postgres:17` image) |
| Claude Code | 2.1.287 |
| Python / uv | 3.12.13 / 0.11.23 |

No `NEEDS CLARIFICATION` item remains. Two findings changed the spec; both were decided by
the maintainer during planning and are recorded in the spec's Clarifications section.

---

## 1. Git hooks on worktree creation and removal

**Decision**

- Auto-provisioning uses the `post-checkout` hook. It is the only hook git runs for
  `git worktree add`.
- The hook runs `wtenv up` only when all three hold: the checkout flag (`$3`) is `1`, the
  previous HEAD (`$1`) is the all-zero object name, and the worktree is a linked worktree
  (`git rev-parse --git-dir` differs from `git rev-parse --git-common-dir`). The third test
  keeps `git clone` from provisioning.
- The hook block always ends with exit status 0 and sends wtenv's output to standard error.
- Nothing can run `down` on removal: git has no hook for it. Removed worktrees are reclaimed
  by `gc`.
- `wtenv hook install` writes into the repository's default hooks directory only
  (`<git-common-dir>/hooks/post-checkout`). It adds a marked block directly after the
  shebang line of an existing POSIX shell hook, or creates the file. If `core.hooksPath`
  points anywhere else, or the existing hook is not a POSIX shell script, the command fails
  with `unsupported` and prints the block so the developer can add it to their hook manager.

**Rationale**

The exit status of `post-checkout` becomes the exit status of `git worktree add`, so a
failing `wtenv up` would make the git command look failed (FR-052 forbids that). The block is
placed after the shebang rather than at the end so that an existing hook that ends with
`exit` or `exec` cannot skip it. Hook directories redirected by `core.hooksPath` are often
tracked directories (husky, lefthook) or shared by many repositories; writing there would
modify tracked files or affect other repositories.

**Limitations** (documented in the quickstart)

1. `git worktree add --no-checkout` and `--orphan` run no hook.
2. Hooks are not cloned or versioned. Each clone installs the hook once; all worktrees of
   that clone share it, because the hooks directory is in the common git directory.
3. A caller can switch hooks off with `-c core.hooksPath=/dev/null`. Claude Code does this
   (section 2).
4. `post-checkout` also fires on `git clone`, `git switch`, and `git checkout`, so the block
   must filter, cheaply and in shell, before it starts Python.
5. The hook inherits the caller's `PATH`. If `wtenv` is not on it, the block reports that on
   standard error and the worktree stays unprovisioned.
6. No hook fires on `git worktree remove`, `move`, `prune`, `lock`, `unlock`, or `repair`.

**Alternatives considered**

- `reference-transaction` hook: fires on every ref update, is noisy, and does not fire on
  removal either.
- A `wtenv worktree add` wrapper command: a new command outside FR-001.
- A watcher process: forbidden by Principle VIII (no daemon).

**Evidence**

- Documented, <https://git-scm.com/docs/githooks#_post_checkout> (identical text in the
  local 2.54 man page): "It is also run after git-clone(1), unless the `--no-checkout`
  (`-n`) option is used. The first parameter given to the hook is the null-ref, the second
  the ref of the new HEAD and the flag is always 1. Likewise for `git worktree add` unless
  `--no-checkout` is used."
- Documented, same page: "By default the hooks directory is `$GIT_DIR/hooks`, but that can
  be changed via the `core.hooksPath` configuration variable". The page lists 28 hooks; none
  is tied to worktree removal.
- Documented, git source `builtin/worktree.c` at tag v2.54.0, lines 607–622
  (<https://github.com/git/git/blob/v2.54.0/builtin/worktree.c>): "Hook failure does not
  warrant worktree deletion, so run hook after is_junk is cleared, but do return appropriate
  code when hook fails", guarded by `if (!ret && opts->checkout && !opts->orphan)`, with
  `GIT_DIR` and `GIT_WORK_TREE` removed from the hook's environment and its directory set to
  the new worktree.
- Observed, with eight hooks instrumented (`post-checkout`, `post-commit`, `post-merge`,
  `pre-commit`, `post-rewrite`, `reference-transaction`, `post-index-change`,
  `pre-auto-gc`):

  | Command | `post-checkout` | Arguments | Working directory |
  |---------|-----------------|-----------|-------------------|
  | `git worktree add ../wt -b b` | fired | `0000…0 <new> 1` | new worktree root |
  | `git worktree add --detach ../wt` | fired | `0000…0 <new> 1` | new worktree root |
  | `git worktree add --no-checkout …` | not fired | | |
  | `git worktree add --orphan …` | not fired | | |
  | `git switch -c x` in a worktree | fired | `<old> <new> 1` | that worktree |
  | `git checkout HEAD -- file` | fired | `<old> <new> 0` | that worktree |
  | `git worktree move`, `lock`, `unlock` | no hook at all | | |
  | `git worktree remove`, `remove --force` | no hook at all | | |
  | `rm -rf` then `git worktree prune` | no hook at all | | |

- Observed: a hook that exits 7 makes `git worktree add` exit 7; the worktree still exists.
- Observed: inside the hook during `git worktree add`, `GIT_DIR` and `GIT_WORK_TREE` are
  unset, and `git rev-parse` resolves the new worktree from the working directory.
- Observed: the hook block in [contracts/files.md](contracts/files.md) ran `wtenv up` for
  `git worktree add` (branch and `--detach`), did not run it for `git switch`, a file
  checkout, or `git clone`, left git's exit status at 0 when `wtenv` exited 6, and printed
  the not-on-`PATH` message when `wtenv` was missing.
- Observed: `git rev-parse --git-path hooks` returns `<common-dir>/hooks` from a linked
  worktree, and `<worktree>/.husky` when `core.hooksPath=.husky`.

---

## 2. Claude Code worktree lifecycle hooks

**Decision**

v1 ships no Claude Code integration. FR-055 and User Story 5 scenario 4 are not delivered
(maintainer decision, 2026-10-03, under the spec's "Claude Code hooks" assumption).

How wtenv works with Claude Code in v1:

- A worktree that Claude Code creates is **not** provisioned automatically. `wtenv up` must
  be run in it. The quickstart shows a one-line `CLAUDE.md` instruction for this, and
  `wtenv exec` fails with `not_provisioned` and a hint naming `wtenv up`, so an agent that
  forgets is told what to do.
- A worktree that Claude Code removes becomes `orphaned` (Claude Code uses
  `git worktree remove`), and `wtenv gc` reclaims it.
- A worktree created by hand with `git worktree add` and then opened in Claude Code is
  provisioned by the git hook as usual.

**Rationale**

Claude Code does expose `WorktreeCreate` and `WorktreeRemove`, but they are replacement
hooks, built for version control systems other than git. A hook that only wants to be told
"a worktree was created" cannot use them without taking over creation and removal itself.
The git hook cannot fill the gap, because Claude Code runs git with hooks switched off.

**Alternatives considered** (both recorded in `docs/roadmap.md`)

| Option | What it would do | Why not in v1 |
|--------|------------------|---------------|
| `SessionStart` hook runs `wtenv up` | Opt-in hook in the main checkout's `.claude/settings.local.json`. Provisions the worktree a session starts in. | Not the lifecycle hooks FR-055 describes. No `down`. Does not cover subagent worktrees or `EnterWorktree` mid-session. Principle X sends it to the roadmap. |
| Replacement hooks | wtenv's `WorktreeCreate` runs `git worktree add` and `wtenv up` and prints the path; its `WorktreeRemove` runs `wtenv down` and `git worktree remove`. | Loses `.worktreeinclude`, base-branch handling, pull-request worktrees, exit cleanup prompts, branch deletion, and the cleanup sweep. Makes wtenv delete worktree directories. |

**Evidence**

- Documented, <https://code.claude.com/docs/en/hooks#worktreecreate>: "By default Claude
  Code creates the isolated working copy with `git worktree`. Configuring a WorktreeCreate
  hook replaces that default git behavior, letting you use a different version control
  system like SVN, Perforce, or Mercurial." and "Because the hook replaces the default
  behavior entirely, `.worktreeinclude` is not processed." and "The hook must return the
  path to the created worktree directory." Its input adds one field, `name`.
- Documented, <https://code.claude.com/docs/en/hooks#worktreeremove>: "This is the cleanup
  counterpart to WorktreeCreate." and "For git-based worktrees, Claude Code handles cleanup
  automatically with `git worktree remove`. If you configured a WorktreeCreate hook, pair it
  with a WorktreeRemove hook to control cleanup of the worktrees it creates" and "Hook exits
  0: the worktree counts as removed. Claude Code reads nothing else from the hook, so make
  sure your hook deleted the directory." Its input adds `worktree_path`, described as "the
  path returned by WorktreeCreate".
- Documented, <https://code.claude.com/docs/en/worktrees>: by default "the worktree is
  created under `.claude/worktrees/<name>/` at your repository root, on a new branch named
  `worktree-<name>`"; "Claude Code skips the repository's own filter drivers when it creates
  a worktree because a filter driver is a shell command"; and "`cwd` follows Claude: the
  `cwd` field in the hook's input JSON is the worktree root".
- Documented, <https://code.claude.com/docs/en/settings>: for
  `.claude/settings.local.json`, "In a worktree, it uses the file at the main checkout's
  root."
- Observed (not documented), with a logging `git` shim on `PATH` and
  `claude -p --worktree wtprobe`: Claude Code ran
  `git -c core.fsmonitor= -c core.hooksPath=/dev/null … worktree add --no-track -B
  worktree-wtprobe <repo>/.claude/worktrees/wtprobe HEAD`, then `git worktree lock`. The
  repository's `post-checkout` hook did **not** run.
- Observed (not documented): a `SessionStart` hook defined only in the main checkout's
  `.claude/settings.local.json` ran with its working directory and its `cwd` field set to
  the new worktree. `SessionEnd` ran the same way.

**Note for users of `.worktreeinclude`**: if it lists the env file, Claude Code copies the
main checkout's file, including wtenv's section with the main checkout's ports, into each
new worktree. The values are wrong until `wtenv up` runs there, which rewrites the section in
place. The quickstart says not to list wtenv's env file in `.worktreeinclude`.

---

## 3. PostgreSQL: `CREATE DATABASE … TEMPLATE`

**Decision**

- wtenv opens one autocommit connection to the `postgres` maintenance database on the shared
  local server. It never connects to the template database.
- Create: `CREATE DATABASE <name> TEMPLATE <template>` with no `STRATEGY` clause.
- Before issuing it, wtenv counts other sessions on the template in `pg_stat_activity`. If
  the count is above zero, it fails at once with `template_in_use` and issues nothing.
- Remove: `DROP DATABASE IF EXISTS <name> WITH (FORCE)`. This needs PostgreSQL 13 or later,
  which is the minimum server version wtenv supports.
- wtenv never terminates sessions on the template and never alters it.

| Server answer | wtenv result |
|---------------|--------------|
| Pre-check finds other sessions on the template | `template_in_use` |
| `55006` object_in_use from `CREATE DATABASE` (a session arrived after the pre-check) | `template_in_use` |
| Template absent in `pg_database`, or `3D000` | `template_missing` |
| Target name exists and is not recorded for this worktree, or `42P04` | `ownership_conflict` |
| Cannot connect, authentication fails, `42501` permission denied, server older than 13 | `dependency_unavailable` |

**Rationale**

- *Pre-check.* PostgreSQL itself waits up to five seconds before it reports a busy template.
  FR-082 requires failure "without retrying or waiting" and a message with the number of
  open connections. The pre-check gives both. `55006` is still mapped, for the race.
- *Maintenance database.* A session connected to the template counts as "another session"
  for every other `CREATE DATABASE`. If wtenv connected to the template, concurrent `up` runs
  would block each other.
- *No `STRATEGY`.* The server default (`WAL_LOG` on 15 and later) is the documented best
  choice for small templates, and the clause does not exist before 15. Measured copies of
  9 MB and 381 MB templates took 0.1–1.3 s with either strategy. FR-063's closed settings
  list has no strategy setting, so a setting would be new surface.

**The fastest safe strategy**

| Template size | Fastest safe choice | Cost |
|---------------|---------------------|------|
| Up to a few hundred MB (typical dev template) | `WAL_LOG`, the default on 15+ | None; no checkpoint |
| Several GB | `FILE_COPY` | Forces a checkpoint before and after |
| Several GB on PostgreSQL 18 with a copy-on-write filesystem (APFS, XFS reflink, Btrfs) | `FILE_COPY` with the server setting `file_copy_method = clone` | As above; the copy itself is near instant |

Both strategies are safe; they differ only in speed and write-ahead-log volume. v1 leaves the
choice to the server. A `strategy` setting is recorded in `docs/roadmap.md`.

**Alternatives considered**

- Terminate the sessions on the template: rejected in clarification (FR-027).
- `pg_dump | pg_restore`: works with live sessions, but is slower, needs the client programs
  installed, and adds a second copy path to test. Rejected in clarification.
- Retry for a bounded time: rejected in clarification; the usual holder is a running app.

**Evidence**

- Documented, <https://www.postgresql.org/docs/current/sql-createdatabase.html> (version
  18): "The principal limitation is that no other sessions can be connected to the template
  database while it is being copied. `CREATE DATABASE` will fail if any other connection
  exists when it starts; otherwise, new connections to the template database are locked out
  until `CREATE DATABASE` completes."
- Documented, same page, `STRATEGY`: "If the `WAL_LOG` strategy is used, the database will
  be copied block by block and each block will be separately written to the write-ahead log.
  This is the most efficient strategy in cases where the template database is small, and
  therefore it is the default. The older `FILE_COPY` strategy is also available. … it also
  forces the system to perform a checkpoint both before and after the creation of the new
  database. … The `FILE_COPY` strategy is affected by the `file_copy_method` setting."
- Documented, same page: "`CREATE DATABASE` cannot be executed inside a transaction block."
- Documented, <https://www.postgresql.org/docs/current/manage-ag-templatedbs.html>: "If this
  flag is set, the database can be cloned by any user with `CREATEDB` privileges; if it is
  not set, only superusers and the owner of the database can clone it."
- Documented, <https://www.postgresql.org/docs/18/runtime-config-resource.html>,
  `file_copy_method`: "Possible values are `COPY` (default) and `CLONE` (if operating
  support is available). … `CLONE` uses the `copy_file_range()` (Linux, FreeBSD) or
  `copyfile` (macOS) system calls".
- Documented, <https://www.postgresql.org/docs/current/sql-dropdatabase.html>, `FORCE`:
  "Attempt to terminate all existing connections to the target database. It doesn't
  terminate if prepared transactions, active logical replication slots or subscriptions are
  present in the target database."
- Documented, PostgreSQL source, `REL_17_STABLE`, `src/backend/commands/dbcommands.c`
  (<https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/commands/dbcommands.c>):
  "ShareLock allows two CREATE DATABASEs to work from the same template concurrently, while
  ensuring no one is busy dropping it in parallel".
- Documented, PostgreSQL source, `src/backend/storage/ipc/procarray.c`,
  `CountOtherDBBackends`: "If there are other backends in the DB, we will wait a maximum of
  5 seconds for them to exit."
- Observed on PostgreSQL 17.11:

  | Test | Result |
  |------|--------|
  | One other session on the template; `CREATE DATABASE … TEMPLATE` issued from `postgres` | `ERROR: 55006: source database "tmpl" is being accessed by other users`, `DETAIL: There is 1 other session using the database.`, after **5.33 s**; nothing created |
  | Pre-check `SELECT count(*) FROM pg_stat_activity WHERE datname = 'tmpl' AND pid <> pg_backend_pid()` | Returns the count at once; also works for a non-superuser role |
  | Five concurrent copies of one template, each session connected to `postgres` | 5 of 5 succeed |
  | Five concurrent copies, each session connected to the template itself | 1 of 5 succeeds; 4 fail with `55006` |
  | Template does not exist | `3D000` |
  | Target name already exists | `42P04` |
  | Role with `CREATEDB` that does not own the template | `42501 permission denied to copy database`; succeeds once the template has `IS_TEMPLATE true` |
  | `CREATE DATABASE` inside `BEGIN` | `25001` |
  | `DROP DATABASE` with a live session / `… WITH (FORCE)` | `55006` / succeeds |
  | `DROP DATABASE IF EXISTS` on a missing database | succeeds |
  | 9 MB template, `WAL_LOG` / `FILE_COPY` | 0.13–0.15 s / 0.14–0.31 s |
  | 381 MB template, `WAL_LOG` / `FILE_COPY` | 0.86–1.34 s / 0.58–1.23 s |
  | New connection to the template during a copy | Waits until the copy ends (0.81 s) |

**Consequence for users**: a role that neither owns the template nor is a superuser can only
copy it if the template is flagged `IS_TEMPLATE`. wtenv does not set the flag (FR-027); the
`dependency_unavailable` message for `42501` says so.

---

## 4. Docker Compose: remapping host ports with an override file

**Decision**

- `up` generates one override file next to the configured compose file, under the name
  Compose loads automatically (`compose.override.yaml` for `compose.yaml`, and the matching
  name for the other three default names). It contains:
  - a top-level `name:` set to the worktree's project name, and
  - for every service that publishes ports, `ports: !override` followed by the full port
    list in long syntax, with only `published` changed.
- `up` reads the compose file through Compose itself:
  `docker compose -f <file> --profile "*" config --format json`, with the worktree's port
  variables in the environment. It never parses YAML.
- **Tying a port to a variable** (FR-031) is done in the compose file, the way Compose users
  already do it: `"${API_PORT:-8000}:8000"`. After resolution with the worktree's variables,
  a published port equal to a variable's port is tied to that variable and keeps it. Every
  other published port gets the next free port of the block, in the order (service name,
  container port, protocol, host IP).
- A port with no host port (`"3000"`) is pinned to a block port too, so it is visible in
  `ls` and never left to chance.
- After writing, `up` verifies with a plain `docker compose --profile "*" config --format
  json` run in the compose file's directory, the way the developer would run it. The project
  name must equal the recorded one and every published port must be an assigned one;
  otherwise `up` removes the override it wrote and fails with `unsupported`.
- Compose 2.24.4 or later is required. Older versions fail with `dependency_unavailable`.
- `down` and `gc` remove a project with `docker compose -p <project> down --volumes
  --remove-orphans`, run from a directory with no compose file, and list containers,
  networks, and volumes by the label `com.docker.compose.project=<project>` before and
  after, to report exactly what was removed.

v1 limits, each failing before anything is changed for the compose step:

| Situation | Result | Why |
|-----------|--------|-----|
| Compose file is not named `compose.yaml`, `compose.yml`, `docker-compose.yaml`, or `docker-compose.yml` | `config_invalid` | Compose only finds these names, and only then loads an override, without `-f`. FR-030 cannot be met otherwise. |
| An override file with one of the four override names already exists and wtenv did not create it | `ownership_conflict` | Compose loads one override file. wtenv does not modify or replace the developer's. |
| `COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` is set in the environment or in `.env` beside the compose file | `unsupported` | Both outrank the override; isolation would silently not apply. |
| A port publishes a host range onto one container port (`"8000-9000:80"`) | `unsupported`, naming the service | Compose picks any free port of the range at start; it cannot be pinned (FR-033). |

**Rationale**

- Only an auto-loaded file satisfies FR-030 ("no extra flags") with the default env file
  `.env.local`, which Compose does not read.
- `ports` is merged by concatenation, so an override without `!override` leaves the original
  host port published as well. `!override` is the documented way to replace the list.
- Compose resolves interpolation, `extends`, `include`, and short syntax. Reading its JSON
  output needs no YAML library and cannot disagree with what Compose will later run.
- `--profile "*"` includes services behind profiles, so a service started later with
  `--profile x` is remapped too.
- The verification step is what makes "never silently un-remapped" (FR-033) a checked fact.

**Alternatives considered**

- `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME` written to an env file: works with any file
  name and beside a developer's override, but Compose reads only `.env`. Recorded in
  `docs/roadmap.md`.
- A new `wtenv.toml` setting that ties a service port to a variable: FR-063's settings list
  is closed.
- PyYAML: a new dependency that still would not resolve interpolation or `extends`.
- The Docker SDK: excluded by the maintainer.
- `!reset` plus a second key: needs more text than `!override` for the same result.
- Removing resources one by one with `docker rm`: `compose down` already orders the stop and
  removal. wtenv falls back to `docker rm`, `docker network rm`, and `docker volume rm` only
  for labelled resources still present afterwards.

**Evidence**

- Documented, <https://docs.docker.com/reference/compose-file/merge/#replace-value>:
  "`!override` allows you to fully replace an attribute, bypassing the standard merge rules"
  with the example `ports: !override` / `- "8443:443"`, and "If `!override` had not been
  used, both `8080:80` and `8443:443` would be exposed". The feature is marked "Requires:
  Docker Compose 2.24.4 and later".
- Documented, <https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/>: "By
  default, Compose reads two files, a `compose.yaml` and an optional `compose.override.yaml`
  file." and "For the multi-value options `ports`, `expose`, `external_links`, `dns`,
  `dns_search`, and `tmpfs`, Compose concatenates both sets of values" and "Using `-f` is
  optional. If not provided, Compose searches the working directory and its parent
  directories for a `compose.yaml` and a `compose.override.yaml` file."
- Documented, <https://docs.docker.com/compose/how-tos/project-name/>: precedence is "1. The
  `-p` command line flag. 2. The COMPOSE_PROJECT_NAME environment variable. 3. The top-level
  `name:` attribute in your Compose file. Or the last `name:` if you specify multiple Compose
  files", and "Project names must contain only lowercase letters, decimal digits, dashes, and
  underscores, and must begin with a lowercase letter or decimal digit."
- Documented, <https://docs.docker.com/compose/intro/compose-application-model/>: "The
  default path for a Compose file is `compose.yaml` (preferred) or `compose.yml` … Compose
  also supports `docker-compose.yaml` and `docker-compose.yml` for backwards compatibility".
- Documented, <https://docs.docker.com/reference/cli/docker/compose/config/>: "It merges the
  Compose files set by `-f` flags, resolves variables in the Compose file, and expands
  short-notation into the canonical format."
- Documented, <https://docs.docker.com/reference/cli/docker/compose/down/>: "Networks and
  volumes defined as external are never removed." and `-v`: "Remove named volumes declared
  in the "volumes" section of the Compose file and anonymous volumes attached to
  containers".
- Documented, <https://docs.docker.com/reference/compose-file/services/#ports>: `published`
  "is defined as a string and can be set as a range using syntax `start-end`. It means the
  actual port is assigned a remaining available port, within the set range."
- Observed on Compose 5.1.4:

  | Test | Result |
  |------|--------|
  | `config --format json` on short-syntax ports | Long syntax: `{"mode","protocol","published" (string),"target" (int)}`, plus `host_ip` when given; `"3000"` has no `published` key |
  | `"9000-9002:9000-9002"` | Expanded to three single mappings |
  | `"7000-7005:7000"` | One mapping with `published: "7000-7005"` |
  | `DB_PORT=25001` in the environment, file says `"${DB_PORT:-5432}:5432"` | `published: "25001"` |
  | Override with plain `ports:` | Original and new ports both published |
  | Override with `ports: !override` | Only the new ports |
  | Override with top-level `name:` | Project name, network name, and volume names all use it |
  | The exact file format in [contracts/files.md](contracts/files.md) | Parsed; correct result |
  | Each of the four override file names beside `compose.yaml` | Loaded automatically |
  | `docker-compose.yml` with `compose.override.yaml` | Loaded automatically |
  | Two override files present | Warning "Found multiple override files with supported names"; the first is used |
  | `-f compose.yaml` given | Override not loaded |
  | `COMPOSE_PROJECT_NAME` in the environment or in `.env` | Beats `name:` in the override |
  | A service behind `profiles:` | Missing from `config`; present with `--profile "*"` |
  | `docker compose up -d` with the override, no flags | Container published only on the remapped ports |
  | Labels | Containers, the default network, and named volumes carry `com.docker.compose.project`; an external volume has no label |
  | `docker compose -p <name> down --volumes --remove-orphans` from an empty directory | Container, network, named volume, and anonymous volume removed; external volume kept |
  | The same after the container was removed by hand | Network and volume still removed |
  | The same a second time | Warning "No resource found to remove", exit 0 |
  | `docker compose config` / `docker context inspect` | About 65 ms / 18 ms |

---

## 5. The name `wtenv` on PyPI

**Decision**: publish as `wtenv`. No alternative name is needed.

**Evidence**

- Observed: `https://pypi.org/pypi/wtenv/json` and `https://pypi.org/simple/wtenv/` both
  return HTTP 404. The look-alikes `wt-env` and `wt_env` also return 404. For comparison,
  `worktree-env` and `worktree-runtime` return 200 (taken).
- Documented, <https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/>:
  "A "pending" publisher does **not** create a project or reserve a project's name **until**
  it is actually used to publish."

**Risk**: a free name is only secured by the first upload. If `wtenv` is taken before the
first release, these three were also unregistered on 2026-10-03: `wtenv-cli`, `wtisolate`,
`gitwtenv`. Publishing an early `0.1.0` once User Story 1 works removes the risk; whether to
do so is the maintainer's choice.

---

## 6. Worktree identity

**Decision**

- One git call per command gives the identity:
  `git rev-parse --path-format=absolute --absolute-git-dir --show-toplevel --git-common-dir`.
- The registry key is the `realpath` of the first line (the git directory). The second line
  is the recorded worktree location; the third is the repository (common git directory).
- All git subprocesses run with git's repository-local environment variables removed
  (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, and the rest of
  `git rev-parse --local-env-vars`), so an inherited variable cannot point wtenv at another
  repository.
- A linked worktree that is moved keeps its key. `up` and `down` update the recorded location
  (FR-084). A repository that is moved gets new keys.
- Git 2.31 or later is required (`--path-format`, and `prunable` in
  `git worktree list --porcelain`).

**Rationale**: the git directory is what git itself uses to tell worktrees apart. It is the
same from any subdirectory and through any symbolic link, and it is what makes "moved to
another path" (FR-045 check 2) detectable.

**Known consequence**: git names a linked worktree's git directory after the worktree's
directory name. When a worktree is removed and another with the same directory name is
created before `gc` runs, git reuses the git directory, and wtenv sees one worktree. The new
one takes over the entry, its port block, and its database (spec edge case). A token stored
in the git directory could tell them apart; that is recorded in `docs/roadmap.md`.

**Alternatives considered**

- Key = git directory plus worktree path: keeps "a moved worktree is a new worktree", but
  leaves the old database stuck as `unverifiable` after every `git worktree move`. Rejected
  by the maintainer on 2026-10-03.
- Key = worktree path alone: a path can be reused by a different repository.

**Evidence**

- Observed:

  | Where the command ran | Git directory | Top level |
  |-----------------------|---------------|-----------|
  | Main worktree, root or subdirectory | `<repo>/.git` | `<repo>` |
  | Linked worktree, root, subdirectory, or through a symlink | `<repo>/.git/worktrees/wt1` | `<wt1>` (resolved) |
  | After `git worktree move wt2 wt2-moved` | `<repo>/.git/worktrees/wt2` (unchanged) | `<wt2-moved>` |
  | After plain `mv wt1 wt1-mv` | `<repo>/.git/worktrees/wt1` (unchanged) | `<wt1-mv>` |
  | Outside a repository, or inside `.git` | exit 128 | |

- Observed: after the plain `mv`, `git worktree list --porcelain` still shows the old path,
  with `prunable gitdir file points to non-existent location`. After `rm -rf`, the same.
  After `git worktree lock`, `locked <reason>`.
- Observed: worktrees `a/same` and `b/same` get git directories `same` and `same1`. After
  `a/same` is removed, a new `c/same` gets `same` again.
- Observed: `git --git-dir=<common-dir> worktree list --porcelain` works from any directory,
  and fails with "not a git repository" when the directory is gone.
- Documented, <https://git-scm.com/docs/git-worktree> (2.56.0): "The private sub-directory's
  name is usually the base name of the linked worktree's path, possibly appended with a
  number to make it unique." and, for the porcelain format, "This format will remain stable
  across Git versions and regardless of user configuration."
- Documented, same page: "If a worktree is on a portable device or network share which is
  not always mounted, lock it to prevent its administrative files from being pruned
  automatically." This is why a missing directory that git still lists is not evidence of
  removal.
- Documented, Git 2.31.0 release notes
  (<https://github.com/git/git/blob/master/Documentation/RelNotes/2.31.0.adoc>): "`git
  rev-parse` can be explicitly told to give output as absolute or relative path with the
  `--path-format=(absolute|relative)` option." and "`git worktree list` now annotates
  worktrees as prunable, shows locked and prunable attributes in --porcelain mode".

---

## 7. Dependencies (Principle VIII: each one justified)

Runtime dependencies are exactly the maintainer's list. Nothing was added.

| Package | Floor | What it is for | Why not the standard library |
|---------|-------|----------------|------------------------------|
| `typer` | 0.27 | Commands, options, help | `argparse` needs hand-written subcommand dispatch and help text; typer keeps each command a plain typed function |
| `pydantic` | 2.13 | `wtenv.toml` validation, registry file, `--json` documents | Validation errors that name the setting (FR-064), and one schema source for the agent contract |
| `psycopg[binary]` | 3.3 | Create and drop databases on the local Postgres server | No Postgres client in the standard library; the `binary` extra ships libpq, so install needs no compiler and no system library |
| `filelock` | 4.0 | Registry lock and worktree locks | Maintainer's choice; a timeout API over `flock` |
| `platformdirs` | 4.12 | The per-user state directory on macOS and Linux | Correct per-platform paths, and `XDG_STATE_HOME` support |

Standard library used instead of a dependency: `tomllib` (config), `json`, `socket` (port
test), `subprocess` (git, docker), `hashlib`, `os`/`pathlib`/`shutil`/`tempfile`.

Development only: `pytest`, `pytest-cov`, `ruff`, `mypy`, `testcontainers`. `hyperfine` is an
external program used for NFR-001.

Deliberately not added: PyYAML (section 4), the Docker SDK (maintainer), `psutil` (section
11, port holders), `click` (see below).

**Facts that affect the implementation**

- Observed: `typer` 0.27.2 ships its own copy of click as `typer._click`; the `click`
  package is **not** installed. wtenv must never `import click`. Usage errors are subclasses
  of the public `typer.exceptions.TyperException` and carry `exit_code == 2`.
- Observed: with `standalone_mode=False`, `app(argv)` returns the exit code of `typer.Exit`
  and raises usage errors, which lets `wtenv.cli.main` own every exit status and print JSON
  errors.
- Observed: `import typer` does not import `rich`, although `rich` is installed with it.
- Observed: `filelock` 4.0.9 uses `flock` on macOS and Linux. `FileLock(path).acquire(
  timeout=…)` raises `filelock.Timeout`. After the holder was killed with `SIGKILL`, another
  process acquired the lock immediately. The option `fallback_to_soft=False` "fails closed,
  letting the `ENOSYS` propagate so a caller that needs kernel-enforced locking is never
  silently downgraded" (its docstring).
- Observed: `platformdirs.user_state_dir("wtenv", appauthor=False)` is
  `~/Library/Application Support/wtenv` on macOS and follows `XDG_STATE_HOME` when it is
  set, on macOS as on Linux. Tests and the quickstart use `XDG_STATE_HOME` to isolate the
  registry; wtenv defines no environment variable of its own.
- Observed: `psycopg.errors` has `ObjectInUse` (55006), `DuplicateDatabase` (42P04),
  `InvalidCatalogName` (3D000), `InsufficientPrivilege` (42501); `psycopg.sql.Identifier`
  quotes names; `psycopg.connect(..., autocommit=True)` exists.

**Note on Principle VIII ("pure-Python package")**: wtenv's own wheel is pure Python.
`pydantic-core` and `psycopg-binary` are compiled, and ship wheels for macOS and Linux on
x86-64 and arm64. Installation stays one command with no compiler.

---

## 8. Startup budget (NFR-001)

**Decision**

- `wtenv/cli.py` imports only `typer` and the standard library at module level. Every command
  function imports its implementation inside the function.
- `--version` reads a constant in `wtenv/__init__.py`; it imports nothing else.
- `psycopg` is imported only by the database step. The compose code is imported only when
  compose isolation is configured. Pydantic models are imported by the commands that read
  the registry or print JSON, not by `--version`.
- A benchmark task runs the NFR-001 `hyperfine` procedure and records the result in
  `docs/benchmarks.md`.

**Evidence** (observed; Python timing loop, best of 5–7 fresh interpreters; hyperfine is not
installed on this machine, so the official measurement is a planned task)

| What | Time |
|------|------|
| Bare interpreter | 19 ms |
| `import typer` | +33 ms |
| `import pydantic` / plus defining and dumping a model | +31 ms / +83 ms |
| `import filelock` (it imports `asyncio`) | +59 ms |
| `import platformdirs` | +11 ms |
| `import psycopg` | +103 ms |
| One `git rev-parse` / one `git worktree list --porcelain` | 21 ms / 12 ms |
| Prototype `--version` (typer app, installed) | 54–61 ms |
| Prototype `ls --json`: typer, lazy pydantic models, filelock, platformdirs, registry read, two git calls, installed console script | **194 ms mean** (185–219 ms, n = 20) |

**Rationale**: the prototype of the slowest measured path is about 100 ms under the 300 ms
limit. `psycopg` alone would use a third of the budget, which is why it is lazy.

**Contingency** (needs the maintainer's approval, because it changes the dependency list):
if the benchmark fails, the largest single saving is replacing `filelock` with a 30-line
`fcntl.flock` helper, about 60 ms.

---

## 9. Locks and their wait bounds

**Decision**

| Lock | File | Taken by | Held for | Wait bound | On timeout |
|------|------|----------|----------|------------|------------|
| Registry | `<state>/registry.lock` | Every command | One read, or one read-check-write | **10 s** | `registry_busy` |
| Worktree | `<state>/locks/<id>.lock`, `<id>` = first 16 hex digits of SHA-256 of the git directory | `up`, `down`; `gc` per entry | The whole `up` or `down`; in `gc`, the re-check and release of one entry | **60 s** for `up` and `down`; `gc` does not wait | `worktree_busy`; `gc` skips the entry |

- Order: a command takes its worktree lock first and the registry lock only for short
  sections inside it. It never waits for a worktree lock while holding the registry lock.
- Both bounds are constants. Functions take them as parameters so tests can pass small
  values; there is no flag and no environment variable.
- Locks are created with `fallback_to_soft=False`. If the state directory's filesystem has
  no `flock`, wtenv stops with `registry_unreadable` instead of using a lock that a killed
  process would leave behind.
- Lock files are never deleted. Deleting a lock file while another process waits on it lets
  two processes hold "the" lock. The files are empty.

**Rationale for the numbers**: a registry section is file I/O and at most a few dozen
`bind` calls, so ten seconds only expires if something is badly wrong. Sixty seconds lets a
second `up` wait out an ordinary database copy while staying under the two-minute command
timeout that coding agents commonly apply.

**Evidence**: section 7 (`filelock` observations), and FR-068, FR-076 to FR-078.

---

## 10. Error codes and exit statuses

**Decision**: one code per category, and one exit status per code, in every command except
`exec`. The full table, with the meaning of each code, is in
[contracts/cli.md](contracts/cli.md).

| Exit | Code | Exit | Code |
|------|------|------|------|
| 0 | success | 10 | `template_in_use` |
| 1 | `internal_error` | 11 | `ownership_conflict` |
| 2 | `usage_error` | 12 | `post_up_failed` |
| 3 | `config_invalid` | 13 | `partial_failure` |
| 4 | `not_in_worktree` | 14 | `registry_busy` |
| 5 | `not_provisioned` | 15 | `worktree_busy` |
| 6 | `no_free_block` | 16 | `registry_unreadable` |
| 7 | `env_file_unusable` | 17 | `problems_found` |
| 8 | `dependency_unavailable` | 18 | `worktree_exists` |
| 9 | `template_missing` | 19 | `unsupported` |

`exec` is a wrapper, so it follows the convention of `env`: 125 when wtenv itself fails, 126
when the command is found but cannot be run, 127 when it is not found, otherwise the
command's own status. The specific code is still in the JSON error and on standard error.

**Rationale**

- 0, 1, and 2 keep their conventional meanings (success, unexpected failure, usage).
- FR-059's twelve categories and the two template codes fixed in the spec each get a code.
  Three codes are added: `internal_error`; `worktree_exists` (FR-073's refusal, so an agent
  knows to run `down` there instead); and `unsupported` (a valid request that v1 cannot
  carry out safely, such as an un-remappable port or a redirected hooks directory).
- In `exec`, a table status would be indistinguishable from the command's own. `pytest`
  exits 5 for "no tests collected"; `not_provisioned` is also 5.

**Alternatives considered**: sysexits values (64–78) have fixed meanings that do not match
wtenv's categories. A separate numeric band for `exec` only adds a second table.

**Evidence**: documented,
<https://www.gnu.org/software/coreutils/manual/html_node/env-invocation.html>: "Exit status:
… 125 if `env` itself fails, 126 if *command* is found but cannot be invoked, 127 if
*command* cannot be found, the exit status of *command* otherwise".

---

## 11. Smaller decisions

**Port range: 20000–29999.**
It lies above the privileged ports and below Linux's default ephemeral range (32768–60999),
macOS's (49152–65535), and Kubernetes' default NodePort range (30000–32767). It holds 1000
blocks of the default size; FR-010 asks for 50. Documented,
<https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html>, `ip_local_port_range`:
"The default values are 32768 and 60999 respectively."

**Port test.** A port is free when a TCP socket can be bound to it on `127.0.0.1`, on
`0.0.0.0`, and on `::1`, without `SO_REUSEADDR`. If `::1` is not available on the host
(`EADDRNOTAVAIL` or `EAFNOSUPPORT`), that address is skipped. UDP is not tested in v1.

**Block search.** Candidates start at `20000 + k × size`. The first candidate that overlaps
no registered block and whose ports are all free is taken. The search and the registry write
happen in one registry transaction, which is what makes concurrent `up` runs safe (SC-005).

**Env values.** A value made only of `A–Z a–z 0–9 _ . / : @ % + = , ~ -` is written bare.
Any other value is written in single quotes, which every common loader reads literally. A
value containing a single quote or a line break is a `config_invalid` error; in a URL such
characters are percent-encoded anyway.

**SQLite.** The template file is copied byte for byte to `<worktree>/.wtenv/<file name>`,
through a temporary file and an atomic rename. A template with a non-empty `-wal` or
`-journal` file beside it is refused with `template_in_use`, because the copy would miss or
corrupt data. On removal, the copy's `-wal`, `-shm`, and `-journal` files are removed with
it. Documented, <https://www.sqlite.org/wal.html>: "The WAL file is part of the persistent
state of the database and should be kept with the database if the database is copied or
moved." Documented, <https://www.sqlite.org/howtocorrupt.html>, section 1.4: "if the journal
file does exist, it must be kept together with the database to avoid corruption." Leaving a
stale journal beside a fresh copy is the mispairing that section describes. This deviation
from Principle II was resolved by constitution v1.0.2.

**Port holders in `doctor`.** A port in use belongs to the worktree when a container of the
worktree's compose project publishes it, or when `lsof` shows the listening process's
working directory inside the worktree. If it belongs to something else, it is a conflict. If
`lsof` is missing or cannot tell, the port is reported as in use with holder unknown, which
is information and not a problem (FR-060b). Observed: `lsof -nP -iTCP:<port> -sTCP:LISTEN
-Fpc` and `lsof -a -p <pid> -d cwd -Fn` give the process and its directory on macOS.
`psutil` would do the same at the cost of a compiled dependency.

**Docker engine must be local (FR-035).** The effective endpoint is `DOCKER_HOST` if set,
otherwise `docker context inspect --format '{{.Endpoints.docker.Host}}'`. `unix://` and
`tcp://` to `localhost`, `127.0.0.1`, or `[::1]` are accepted; anything else is
`dependency_unavailable`. The check runs before any command that contacts the engine.

**Postgres must be local (FR-025).** The URL pattern must name the host, and the host must be
`localhost`, `127.0.0.1`, or `::1`. Unix-socket URLs are not accepted in v1 (roadmap).

**Tests against Postgres and Compose.** `testcontainers` starts `postgres:17` for the
database tests and runs the sample compose stack. A session fixture runs `docker info` once;
when it fails, every test that needs Docker is skipped with the message "Docker is not
available". Coverage for NFR-003 is taken from the CI job where Docker is available.

**Build and release.** `hatchling` builds the wheel; `uv build` and `uv publish` run in a
GitHub Actions workflow triggered by `v*` tags. Documented,
<https://docs.astral.sh/uv/guides/integration/github/#publishing-to-pypi>: "The workflow
uses Trusted Publishing, so no credentials need to be configured." and "This example workflow
uses two separate jobs (`build` and `publish`) so that the publishing step (which has access
to a publishing credential via `id-token: write`) does not share its permissions with the
building step." Documented, <https://docs.pypi.org/trusted-publishers/>: Trusted Publishing
"eliminates the need to use manually generated API tokens". The first release uses a
"pending" publisher (section 5).
