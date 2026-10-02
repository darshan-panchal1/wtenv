# Feature Specification: wtenv — Per-Worktree Runtime Isolation

**Feature Branch**: `001-worktree-runtime-isolation`

**Created**: 2026-10-03

**Status**: Draft

**Input**: User description: "Build `wtenv`, a CLI that isolates the *runtime* of each git worktree so several coding agents can run the same app in parallel without colliding." The full description (problem, six prioritised user stories, configuration, success criteria, measurable non-functional requirements, and v1 exclusions) is reflected in the sections below.

## Overview

Git worktrees isolate files, but every worktree of a repository still shares the same
ports, the same local database, the same docker-compose project name and volumes, and the
same env files. When two coding agents run dev servers or migrations at the same time, they
collide or corrupt each other's database. Developers work around this with hand-rolled
scripts that leak orphaned databases and containers.

`wtenv` gives each worktree its own runtime and cleans up after it.

**Users**: developers running 2–5 coding agents (Claude Code, Codex, Cursor) in parallel,
one per worktree; solo developers and small startup teams. Most invocations come from
agents and scripts, not from a person at a keyboard.

**Terms used in this document**

- **Worktree**: any git worktree of a repository, including the main one.
- **Provisioned**: a worktree for which `wtenv up` has completed.
- **Registry**: wtenv's own record of everything it has allocated and created.
- **Block**: the contiguous set of ports reserved for one worktree.
- **Orphaned**: recorded in the registry, and git confirms that the worktree was removed
  (the three checks in FR-045 all hold).
- **Unverifiable**: recorded in the registry, the worktree cannot be found as recorded, but
  its removal is not confirmed. Examples: its drive is not mounted, its repository was moved
  or deleted, its directory was deleted by hand while git still lists it, or it was moved
  and no `up` or `down` has run in it since.

## Clarifications

### Session 2026-10-03

Decided by the maintainer before the session (not asked):

- Q: Which git events does the auto-provisioning hook act on? → A: Worktree creation only,
  where it runs `up`. Worktrees removed through plain git are reclaimed by `gc`. Automatic
  `down` exists only through the Claude Code integration, and only if planning confirms
  that Claude Code exposes a worktree-removal hook.
- Q: Where do Postgres worktree databases live? → A: On one shared local Postgres server.
  Each worktree gets its own database, cloned from the template database.
- Q: Do a compose project's containers and volumes count as created by wtenv? → A: Yes,
  when the project is recorded in the registry (constitution v1.0.1, Principle II).
- Q: Does `up` start containers? → A: No.
- Q: How are generated files kept out of `git status`? → A: Through the repository's
  `.git/info/exclude`.
- Q: Which areas does the NFR-003 coverage threshold apply to? → A: The seven areas already
  listed, plus env-file writing.

Asked during the session:

- Q: What evidence must `gc` have before it removes a worktree's resources, and should a
  plain `gc` delete or only preview? → A: `gc` releases an entry only when the recorded
  repository is still on disk, git in that repository no longer has the worktree (not at
  the recorded path and not moved elsewhere), and nothing exists at the recorded path.
  Every other entry is kept as `unverifiable` and is released only when its path is named
  explicitly. A plain `gc` deletes; `--dry-run` previews; there is no first-run preview.
- Q: When two agents run `up`, `down`, or `gc` at the same time, which lock does each
  command hold, and for how long? → A: Two locks. The registry lock is taken by every
  command and held only for one read or read-check-write, never during slow work. A
  per-worktree lock is held by `up` and `down` from start to finish and by `gc` for each
  entry it releases. A second `up` or `down` on the same worktree waits a bounded time,
  then fails with `worktree busy`. `gc` does not wait; it skips busy entries.
- Q: When the env file already has the developer's own lines, how does wtenv add its values
  and mark the lines it owns? → A: Merge. wtenv owns one block between a begin and an end
  comment marker, appended at the end of the file on first write and rewritten in place
  afterwards. Lines outside the markers are never changed. Damaged markers make `up` fail
  without changing anything.
- Q: How many ports should a block hold by default, and what should `up` do when a worktree
  needs more ports than its block has? → A: The default stays 10. An over-full block is a
  configuration error that states the smallest block size that fits and changes nothing.
  wtenv never grows or moves a block on its own; the developer raises the block size in
  `wtenv.toml`.
- Q: What should `up` do when the Postgres template database has active connections and
  cannot be copied, and which error does it return? → A: Fail at once. `up` makes one
  attempt, does not retry, and never closes connections to the template. Nothing is created
  for the database step, the worktree is left `incomplete`, and a later `up` completes it.
  The error is its own stable category with the JSON code `template_in_use`.

Decided during planning (evidence in `research.md`, values in `plan.md` and `contracts/`):

- Q: A linked worktree keeps its git directory when it is moved, so which rule decides its
  identity? → A: The git directory is the identity. A moved linked worktree keeps its port
  block and database, and the next `up` or `down` in it records the new location. Until
  then it shows as `unverifiable` (moved) and `gc` leaves it alone. A moved repository
  still gets new identities, because its git directories change.
- Q: Claude Code's worktree hooks replace its own git logic instead of notifying, and it
  disables git hooks when it creates a worktree. What does v1 do? → A: The Claude Code
  integration is dropped from v1, as the "Claude Code hooks" assumption allows. FR-055 and
  User Story 5 scenario 4 are not delivered. v1 has no automatic `down`.
- Q: Does `down` remove wtenv's lines from `.git/info/exclude`? → A: Only when the last
  registered worktree of that repository is torn down.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Port and env isolation (Priority: P1)

A developer or agent working in a worktree runs `wtenv up`. The worktree receives a block of
ports that no other worktree has, and an env file in which the app's port variables are set
to ports from that block. Running the command again changes nothing.

**Why this priority**: port collisions are the first thing that breaks when two agents run
the same app. Every other story builds on the block and the env file. This story needs no
configuration, so it is useful on its own.

**Independent Test**: in a repository with no `wtenv.toml`, create two worktrees, run
`wtenv up` in each, and start a dev server in each that reads `PORT` from the env file.
Both servers run at the same time. Running `up` again in either worktree leaves its ports
and env file unchanged.

**Acceptance Scenarios**:

1. **Given** a repository with no `wtenv.toml` and a worktree that has never been
   provisioned, **When** `wtenv up` is run in it, **Then** a block is allocated, `.env.local`
   in the worktree root sets `PORT` to a port in that block, and the command reports the
   allocation.
2. **Given** two worktrees of the same repository, **When** `wtenv up` is run in both,
   including at the same moment, **Then** their blocks share no port.
3. **Given** a provisioned worktree, **When** `wtenv up` is run again with unchanged
   configuration, **Then** the ports are the same, the env file is byte-identical, and
   nothing new is created.
4. **Given** an unrelated process is listening on a port, **When** `wtenv up` allocates a
   block, **Then** the block contains no port that was in use at allocation time.
5. **Given** a `wtenv.toml` that lists `PORT`, `API_PORT`, and `VITE_PORT` with a block size
   of 10, **When** `wtenv up` is run, **Then** each variable is set to a different port in
   the block, assigned in the configured order, and the assignment is the same on every run.
6. **Given** `.env.local` already contains lines written by the developer, **When**
   `wtenv up` is run, **Then** those lines are unchanged and wtenv's values appear after
   them, at the end of the file, between wtenv's begin and end marker lines.
7. **Given** a `wtenv.toml` that sets a different env file path, **When** `wtenv up` is run,
   **Then** the values are written to that path and `.env.local` is not created.

---

### User Story 2 - Database isolation (Priority: P2)

A developer whose app uses a database enables database isolation in `wtenv.toml`. After
`wtenv up`, the worktree has its own database, created from a configured template, and the
database URL in its env file points to it. Migrations or test data in one worktree never
affect another worktree or the template.

**Why this priority**: a shared database is where agents do real damage to each other:
migrations that conflict, and rows that disappear. It comes after P1 because it needs
configuration and the env file from P1.

**Independent Test**: configure Postgres with a template database, run `wtenv up` in two
worktrees, apply a migration in one, and confirm the other worktree and the template are
unchanged. Run `wtenv down` in one worktree and confirm that only its database was removed.
Repeat with SQLite.

**Acceptance Scenarios**:

1. **Given** Postgres isolation is configured with a template database, **When** `wtenv up`
   is run in a worktree, **Then** a new database exists that is a copy of the template, and
   `DATABASE_URL` in the env file points to it.
2. **Given** two provisioned worktrees, **When** a schema change is applied in one,
   **Then** the other worktree's database and the template are unchanged.
3. **Given** a provisioned worktree whose database contains data, **When** `wtenv up` is run
   again, **Then** the same database is kept, it is not re-created, and its data is intact.
4. **Given** SQLite isolation is configured with a template file, **When** `wtenv up` is
   run, **Then** the worktree has its own copy of the file, `DATABASE_URL` points to the
   copy, and changes to it do not affect the template or other worktrees.
5. **Given** a provisioned worktree, **When** `wtenv down` is run, **Then** the database
   wtenv created for it is removed, and the template and every other database are untouched.
6. **Given** a database with the name wtenv would use already exists and wtenv has no record
   of creating it, **When** `wtenv up` is run, **Then** the command fails with a stable
   error and the existing database is not modified.
7. **Given** Postgres isolation is configured but the server cannot be reached, **When**
   `wtenv up` is run, **Then** the command fails with a stable "dependency unavailable"
   error, and running `up` again once the server is back completes the provisioning.
8. **Given** another session is connected to the Postgres template database, **When**
   `wtenv up` is run, **Then** the command fails at once with the `template_in_use` error,
   the other session is still connected, no database was created, and the worktree shows as
   `incomplete`; and **When** `up` is run again after that session has closed, **Then** the
   provisioning completes.

---

### User Story 3 - docker-compose isolation (Priority: P3)

A developer whose app uses docker-compose points `wtenv.toml` at the compose file. After
`wtenv up`, the worktree has its own compose project name and a generated override that
moves every host port into the worktree's block. `docker compose up` in two worktrees then
runs two independent stacks.

**Why this priority**: compose stacks collide on host ports, container names, networks, and
volumes. It comes after P1 and P2 because it depends on the port block and because not
every app uses compose.

**Independent Test**: with a compose file that publishes at least one host port and
declares a named volume, run `wtenv up` and then `docker compose up` in two worktrees. Both
stacks start, each on ports inside its own block, with separate containers, networks, and
volumes.

**Acceptance Scenarios**:

1. **Given** compose isolation is configured, **When** `wtenv up` is run, **Then** the
   worktree has a compose project name that no other worktree has, and a generated override
   in which every host-published port is mapped to a port in the worktree's block.
2. **Given** two provisioned worktrees, **When** `docker compose up` is run in both with no
   extra flags, **Then** both stacks run at the same time with no clash of ports,
   containers, networks, or volumes.
3. **Given** the configuration ties a service's published port to a port variable, **When**
   `wtenv up` is run, **Then** the env file and the override carry the same port number for
   it, so the app can reach the service.
4. **Given** a provisioned worktree, **When** `wtenv up` is run again, **Then** the project
   name and the override are identical to the previous run.
5. **Given** compose isolation is configured, **When** `wtenv up` is run, **Then** the
   committed compose file is not modified.

---

### User Story 4 - Lifecycle and cleanup (Priority: P4)

A developer finishes with a worktree and runs `wtenv down` to release everything wtenv made
for it. When worktrees are removed without `down`, `wtenv gc` finds what they left behind
and removes it, but only once git confirms the worktree is gone. `wtenv ls` shows every
worktree with its ports, database, compose project, and status.

**Why this priority**: without cleanup, the tool recreates the problem it exists to solve:
orphaned databases and containers. It comes after P1–P3 because there must be resources to
clean up.

**Independent Test**: provision three worktrees, run `wtenv down` in one, remove the other
two with `git worktree remove` without running `down`, then run `wtenv gc`. Afterwards no
database, container, volume, or registry entry belonging to the three worktrees remains,
and nothing else on the machine was touched. Separately, provision a worktree, delete its
directory by hand so that git still lists it, and run `wtenv gc`: its database and registry
entry are still there.

**Acceptance Scenarios**:

1. **Given** a provisioned worktree, **When** `wtenv down` is run in it, **Then** its port
   block is released; its database, its compose project's containers, networks, and volumes,
   its generated override, and wtenv's section of its env file are removed; its registry
   entry is removed; and the command reports each item it removed.
2. **Given** a provisioned worktree, **When** `wtenv down --dry-run` is run, **Then**
   nothing changes and the command lists exactly what `down` would remove.
3. **Given** provisioned worktrees that were removed with `git worktree remove`, **When**
   `wtenv gc` is run, **Then** their resources and registry entries are removed and the
   command reports each item it removed.
4. **Given** orphaned worktrees exist, **When** `wtenv gc --dry-run` is run, **Then**
   nothing changes and the command lists exactly what `gc` would remove.
5. **Given** a mix of existing, orphaned, and unverifiable worktrees, **When** `wtenv gc`
   is run, **Then** only the orphaned ones are released, and nothing belonging to an
   existing or unverifiable worktree is touched.
6. **Given** several worktrees in different states, **When** `wtenv ls` is run, **Then**
   every worktree is listed with its ports, database, compose project, and status.
7. **Given** a worktree that was never provisioned, **When** `wtenv down` is run in it,
   **Then** the command succeeds and changes nothing.
8. **Given** a provisioned worktree that cannot be found but whose removal git does not
   confirm (its drive is not mounted, its repository was renamed, moved, or deleted, or its
   directory was deleted by hand while git still lists it), **When** `wtenv gc` is run,
   **Then** nothing of it is removed, and it is reported as `unverifiable` with the reason.
9. **Given** an unverifiable worktree, **When** `wtenv gc` is run with that worktree's
   recorded path named explicitly, **Then** its resources and registry entry are removed
   and the command reports each item it removed.

---

### User Story 5 - Auto-provisioning and exec (Priority: P5)

A developer opts in to automatic provisioning. From then on, creating a worktree provisions
it without anyone running `up`. `wtenv exec -- <command>` runs any command with the
worktree's variables set, for tools that do not read env files.

**Why this priority**: agents create worktrees on their own and will not remember to run
`up`. It is a convenience on top of P1–P4, which already work when run by hand.

**Independent Test**: install the hook, run `git worktree add`, and confirm the new worktree
is provisioned. Run `wtenv exec -- <a command that prints its environment>` and confirm the
worktree's variables are present.

**Acceptance Scenarios**:

1. **Given** the git hook is installed, **When** a worktree is created with
   `git worktree add`, **Then** the new worktree is provisioned as if `wtenv up` had been
   run in it.
2. **Given** the git hook is installed and provisioning fails, **When** a worktree is
   created, **Then** the worktree is still created, and the provisioning error is reported.
3. **Given** the git hook is installed, **When** a branch is switched inside an existing
   worktree, **Then** the hook does nothing.
4. *Not delivered in v1 (see FR-055).* **Given** the Claude Code integration is installed,
   **When** Claude Code creates a worktree, **Then** it is provisioned; and **When** Claude
   Code removes it, **Then** its resources are released as `wtenv down` would.
5. **Given** a provisioned worktree, **When** `wtenv exec -- <command>` is run, **Then** the
   command sees the worktree's variables, and wtenv exits with the command's exit status.
6. **Given** a worktree that is not provisioned, **When** `wtenv exec -- <command>` is run,
   **Then** wtenv fails with a stable error and does not run the command.
7. **Given** the repository already has a hook of the same kind, **When** the wtenv hook is
   installed and later uninstalled, **Then** the existing hook's content is preserved
   throughout.

---

### User Story 6 - Diagnostics (Priority: P6)

An agent or developer needs to read wtenv's results reliably and find out why something is
wrong. Every command accepts `--json`. `wtenv doctor` reports port conflicts, stale registry
entries, and missing Docker or Postgres.

**Why this priority**: it makes the other stories dependable for unattended use. The
`--json` and stable-error behaviour is built into each command as that command is delivered;
this story adds `doctor` and completes the contract.

**Independent Test**: run every command with `--json` and parse its standard output as a
single JSON document. Create each kind of problem `doctor` checks for and confirm it is
reported with its own code and that `doctor` changed nothing.

**Acceptance Scenarios**:

1. **Given** any wtenv command, **When** it is run with `--json`, **Then** standard output
   contains exactly one JSON document and the exit status reflects the result.
2. **Given** a command that fails, **When** it is run with `--json`, **Then** the JSON
   document carries a stable error code and a message.
3. **Given** a healthy machine, **When** `wtenv doctor` is run, **Then** it reports no
   problems and exits with status 0.
4. **Given** a port in a worktree's block is held by a process outside that worktree,
   a registry entry whose worktree is gone, and a configuration that needs Docker or
   Postgres while that dependency is unavailable, **When** `wtenv doctor` is run, **Then**
   each problem is reported with its own stable code, the exit status is the stable
   "problems found" status, and nothing has been changed.

---

### Edge Cases

- **Two `up` runs at once in the same worktree**: the second waits for the first, then runs
  and finds the work done. Both end with the same result as a single run. No resource is
  created twice.
- **`up` and `down` at once in the same worktree**: they run one after the other, in the
  order they obtained the worktree lock. The result is that of running them in that order.
- **A second `up` or `down` waits longer than the bound** (for example behind a long
  database copy): it fails with the stable worktree-busy error and changes nothing.
- **`gc` meets an entry whose worktree lock is held**: `gc` does not wait. It skips the
  entry, reports it as busy, and carries on with the others.
- **A command is killed while it holds a lock**: the lock does not outlive the process.
  The next command is not blocked, and recovers as described for an interrupted `up`.
- **No free block left**: `up` fails with a stable error and allocates nothing.
- **A worktree needs more ports than its block holds** (port variables plus compose
  published ports): `up` fails with a configuration error that states the smallest block
  size that fits. The existing block, env file, and resources are unchanged. After the
  developer raises the block size in `wtenv.toml`, the next `up` moves the worktree to a
  new block.
- **A port in the worktree's block is taken by another process after allocation**: `up`
  keeps the block. `doctor` reports the conflict.
- **`up` is interrupted partway**: running `up` again completes it; running `down` removes
  whatever was created. No resource exists that the registry does not know about.
- **The developer defines a wtenv-managed variable outside wtenv's section of the env
  file**: wtenv leaves the developer's line alone and warns about the duplicate.
- **The developer edits inside wtenv's section**: the next `up` restores the section. The
  rest of the file is untouched.
- **The developer moves wtenv's section, or adds lines after it**: the next `up` rewrites
  the section where it now sits. It is not moved back to the end.
- **wtenv's markers are damaged** (a begin marker with no end, an end marker with no begin,
  or more than one section): `up` fails with the stable env-file error. `down` reports the
  section as not removed and exits with the partial-failure status. Both leave the file as
  it is. wtenv does not guess where its section ends.
- **The env file path is a directory, or is not writable**: `up` fails with the stable
  env-file error.
- **The template database or template file is missing**: `up` fails with the
  `template_missing` error; nothing is created for the database step.
- **The Postgres template database has active connections**: `up` fails at once with the
  `template_in_use` error. It does not retry and does not close those connections. Nothing
  is created for the database step, the port block is kept, and the worktree is
  `incomplete` until a later `up` succeeds.
- **The database is still in use when `down` or `gc` removes it**: the database belongs to
  wtenv, so remaining connections are closed and it is removed.
- **A recorded resource has already been deleted by hand**: `down` and `gc` treat it as
  already released and report it as such. It is not an error.
- **Database credentials are not available when `gc` runs**: that database is reported as
  not removed and stays in the registry; the exit status signals partial failure.
- **A resource has a wtenv-style name but is not in the registry**: wtenv never removes it.
- **The compose file declares volumes or networks as external**: they are shared on purpose
  and are never removed.
- **A compose service sets a fixed container name**: two worktrees cannot both run it. `up`
  warns and names the service.
- **A compose port definition wtenv cannot remap**: `up` fails and names the service. It
  never leaves a port silently un-remapped.
- **The repository already has its own compose override file**: wtenv does not modify or
  replace it, and it keeps taking effect. If wtenv cannot provide isolation alongside it,
  `up` fails with a stable error before changing anything for the compose step.
- **`wtenv.toml` differs between branches**: each worktree is provisioned from the
  `wtenv.toml` in that worktree.
- **`wtenv.toml` changes after provisioning**: the next `up` applies the change without
  touching the existing database.
- **A linked worktree is moved or renamed** (with `git worktree move`, or by hand): it keeps
  its identity, its port block, and its database. Until `up` or `down` is run in it at the
  new location, its entry is unverifiable and `gc` does not touch it. The next `up` or
  `down` there records the new location and reports the move.
- **A repository directory is moved or renamed, or one of its parent directories is
  renamed**: the git directories of its worktrees change, so wtenv treats them as new
  worktrees. The old entries become unverifiable. `gc` keeps them and their databases until
  they are named explicitly.
- **A worktree is removed and git gives a later worktree the same git directory, before
  `gc` has run** (for example a worktree created again under the same directory name): wtenv
  cannot tell the two apart. The new worktree takes over the old entry, including its port
  block and its database. Running `gc` in between, or `down` and then `up`, gives it a
  fresh database.
- **A tool creates a worktree with git hooks switched off** (Claude Code does): the
  auto-provisioning hook does not run. The worktree stays unprovisioned until `wtenv up` is
  run in it.
- **A worktree directory is deleted by hand while git still lists it**: it is unverifiable,
  because git cannot tell this apart from an unmounted drive. It becomes orphaned once the
  developer runs `git worktree prune`. wtenv never runs `git worktree prune` itself.
- **A worktree is on a drive that is not mounted when `gc` runs**: git still lists it, so
  it is unverifiable and nothing of it is removed.
- **A repository is deleted, with or without its worktrees**: `gc` cannot ask git about it,
  so its entries are unverifiable and nothing of them is removed.
- **A worktree reappears between `gc` deciding and `gc` deleting** (for example a worktree
  is created again at the same path): `gc` repeats the checks immediately before it deletes
  and skips the entry if they no longer hold.
- **A post-up command fails**: `up` exits with a stable error naming the command. The
  provisioned resources remain, so the developer can fix the problem and run `up` again.
- **The registry file is damaged**: wtenv stops with a stable error. It does not guess and
  does not delete anything.
- **A command is run outside any git repository**: `up`, `down`, and `exec` fail with a
  stable error. `ls`, `gc`, and `doctor` still work.

## Requirements *(mandatory)*

### Functional Requirements

**Commands and general behaviour**

- **FR-001**: wtenv v1 MUST provide exactly these commands: `up`, `down`, `gc`, `ls`,
  `exec`, `doctor`, an install and uninstall action for auto-provisioning, and `--version`.
  Any other idea MUST be recorded in `docs/roadmap.md` instead of being built.
- **FR-002**: `up`, `down`, and `exec` MUST act on the worktree that contains the current
  directory, from any subdirectory. The main worktree MUST be treated the same as any other
  worktree. Outside a worktree, these commands MUST fail with a stable error and change
  nothing.
- **FR-003**: `ls`, `gc`, and `doctor` MUST work from any directory, including outside a git
  repository.
- **FR-004**: wtenv commands MUST NOT wait for input. v1 defines no interactive prompts.
- **FR-005**: With no `wtenv.toml` present, `up` MUST provide port and env-file isolation
  using defaults: one variable named `PORT`, a block size of 10, and the env file
  `.env.local` in the worktree root. Database and compose isolation MUST stay off unless
  enabled in `wtenv.toml`.

**Worktree identity**

- **FR-006**: Each worktree MUST have one identity: the resolved location of its git
  directory, which git keeps separate for every worktree and which lies inside its
  repository. Two worktrees MUST never share an identity, and the same worktree MUST resolve
  to the same identity no matter which subdirectory or path spelling (for example a symbolic
  link) is used to reach it. wtenv MUST also record the worktree's resolved location on
  disk.
- **FR-084**: When `up` or `down` runs in a worktree whose identity is already in the
  registry under a different location, wtenv MUST treat it as the same worktree, MUST keep
  its port block, database, and compose project, MUST record the new location, and MUST
  report the move.

**Port allocation**

- **FR-007**: `up` MUST allocate one contiguous block of ports for the worktree. The block
  size comes from configuration and defaults to 10.
- **FR-008**: A block MUST NOT share any port with any other block in the registry, across
  all repositories on the machine.
- **FR-009**: At allocation time every port in the block MUST be free. A candidate block
  that contains a port in use MUST be skipped.
- **FR-010**: Blocks MUST come from a fixed range that excludes privileged ports and the
  default ephemeral port ranges of macOS and Linux. The range MUST hold at least 50 blocks
  of the default size.
- **FR-011**: Once allocated, a worktree's block MUST stay the same until it is released by
  `down` or `gc`, or replaced under FR-065. Running `up` again MUST NOT change it, even if
  some of its ports are now in use. wtenv MUST NOT grow or move a block on its own, and
  MUST NOT assign a worktree any port outside its block.
- **FR-012**: When no block is free, `up` MUST fail with a stable error and allocate
  nothing.
- **FR-013**: Invocations that run at the same time in different worktrees MUST all
  succeed and MUST NOT receive overlapping blocks.
- **FR-014**: Each configured port variable MUST receive a different port from the block,
  assigned in the configured order. Configuring more variables than the block can hold MUST
  be a configuration error that states the smallest block size that would fit, and `up`
  MUST change nothing.

**Env file**

- **FR-015**: `up` MUST write the worktree's variables to its env file: by default
  `.env.local` in the worktree root, or the path set in configuration.
- **FR-016**: wtenv MUST merge its values into the env file and MUST NOT overwrite the
  file. It MUST keep all its values in exactly one section, delimited by these two comment
  lines, and MUST preserve everything outside that section byte for byte. If the file does
  not exist, wtenv MUST create it.

  ```text
  # >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>
  # <<< wtenv managed <<<
  ```

- **FR-079**: When the env file has no wtenv section, `up` MUST append the section at the
  end of the file. When a section exists, `up` MUST rewrite it where it is. A line break or
  blank line that wtenv adds to separate its section from the developer's lines counts as
  part of the section. `down` MUST remove the section, including both marker lines, and
  nothing else.
- **FR-080**: When a variable that wtenv manages is also defined outside wtenv's section,
  wtenv MUST leave that line unchanged and MUST warn, naming the variable.
- **FR-081**: When the markers are damaged (a begin marker with no end, an end marker with
  no begin, or more than one section), or the env file path is a directory or is not
  writable, `up` MUST fail with the stable env-file error before it creates anything, and
  MUST leave the file unchanged. `down` MUST leave the file unchanged and MUST report the
  section as not removed, as FR-042 describes.
- **FR-017**: Running `up` again with unchanged configuration MUST produce a byte-identical
  env file.
- **FR-018**: Files that wtenv generates inside a worktree MUST NOT show up as untracked
  changes in git. wtenv MUST achieve this by adding its own entries to the repository's
  `.git/info/exclude`, MUST preserve every other line of that file, and MUST NOT modify
  `.gitignore` or any other tracked file.
- **FR-019**: An env file created by wtenv MUST be readable and writable only by its owner.
  Credentials MUST NOT be stored in the registry and MUST NOT appear in any wtenv output.

**Database isolation**

- **FR-020**: When Postgres isolation is configured, `up` MUST create a database for the
  worktree on the one shared local Postgres server, as a copy of the configured template
  database on that server, and MUST set the database URL variable (default `DATABASE_URL`)
  in the env file from the configured URL pattern.
- **FR-021**: When SQLite isolation is configured, `up` MUST copy the configured template
  file to a location that belongs to the worktree, and MUST set the database URL variable to
  point to the copy.
- **FR-022**: The database name or file location MUST be the same on every run for a given
  worktree, MUST differ between worktrees, and MUST be recognisable as created by wtenv.
- **FR-023**: When the worktree's database already exists and is recorded in the registry,
  `up` MUST leave it and its data untouched.
- **FR-024**: When a database or file already exists at the target name or location and the
  registry does not record wtenv creating it for this worktree, `up` MUST fail with a stable
  ownership-conflict error and MUST NOT modify it.
- **FR-025**: wtenv MUST connect only to database servers on the local machine. A
  configuration that points anywhere else MUST be rejected.
- **FR-026**: Database credentials MUST be suppliable from the developer's environment, so
  that they need not be committed in `wtenv.toml`.
- **FR-027**: wtenv MUST NOT modify or remove the template database or template file, and
  MUST NOT close connections to the template database.
- **FR-082**: When the Postgres template database cannot be copied because other sessions
  are connected to it, `up` MUST fail after a single attempt, without retrying or waiting,
  with the stable `template_in_use` error. The message MUST name the template and the
  number of open connections. `up` MUST NOT create a database, MUST NOT record anything for
  the database step, and MUST NOT write the database URL variable. The port block MUST stay
  allocated, the worktree MUST show as `incomplete`, and a later `up` MUST complete the
  provisioning once the template is free.
- **FR-083**: When the configured template database or template file does not exist, `up`
  MUST fail with the stable `template_missing` error and MUST create nothing for the
  database step.

**docker-compose isolation**

- **FR-028**: When compose isolation is configured, `up` MUST give the worktree a compose
  project name that is the same on every run and that no other worktree has.
- **FR-029**: `up` MUST generate an override that maps every host-published port in the
  configured compose file to a port in the worktree's block. Ports inside containers MUST
  stay as they are. The committed compose file MUST NOT be modified.
- **FR-030**: After `up`, the standard compose commands run from the worktree with no extra
  flags MUST use the worktree's project name and remapped ports.
- **FR-031**: The developer MUST be able to make a configured port variable and a service's
  published port resolve to the same port number. Published ports that are not tied to a
  variable MUST receive remaining ports in the block in a fixed order and MUST be shown in
  the output of `up` and `ls`.
- **FR-032**: When the block cannot hold all port variables and published ports, `up` MUST
  fail with a configuration error that states the smallest block size that would fit, and
  MUST change nothing. This applies to a first `up` and to an `up` in a worktree that is
  already provisioned.
- **FR-033**: When a published-port definition cannot be remapped, `up` MUST fail and name
  the service. A port MUST never be left silently un-remapped.
- **FR-034**: `up` MUST NOT start containers. Starting them is left to the developer or to a
  post-up command.
- **FR-035**: wtenv MUST talk only to the Docker engine on the local machine.

**Post-up commands**

- **FR-036**: Configured post-up commands MUST run in the configured order, from the
  worktree root, with the worktree's variables set, after all provisioning steps, on every
  successful `up`.
- **FR-037**: When a post-up command fails, `up` MUST stop, exit with a stable error that
  identifies the command and its exit status, and leave the provisioned resources in place.

**Teardown (`down`)**

- **FR-038**: `down` MUST release everything the registry records for the current worktree:
  its port block; its database; its compose project's containers, networks, and volumes;
  its generated override; wtenv's section of its env file (and the env file itself when
  wtenv created it and nothing else is in it); and its registry entry.
- **FR-085**: `down` and `gc` MUST remove wtenv's entries from the repository's
  `.git/info/exclude` only when they release the last registered worktree of that
  repository. Until then the entries MUST stay, because the file is shared by every
  worktree of the repository.
- **FR-039**: `down` and `gc` MUST remove only what wtenv created and recorded in the
  registry. A matching name alone MUST never be enough. For compose, this means the
  containers, networks, and volumes that belong to a project recorded in the registry;
  volumes and networks declared external MUST never be removed. For SQLite, this also covers
  the `-wal`, `-shm`, and `-journal` files in the same directory as a recorded SQLite
  database file, named as the database file name plus that suffix (constitution v1.0.2,
  Principle II). These side files MUST be deleted only together with the database file,
  never on their own, and only when the database file itself is recorded in the registry.
- **FR-040**: `down`, `gc`, and the uninstall action MUST support `--dry-run`, which changes
  nothing and lists exactly what would be removed.
- **FR-041**: `down` and `gc` MUST report exactly what they removed, what was already
  absent, and what they could not remove.
- **FR-042**: When some items cannot be removed, they MUST stay recorded in the registry,
  the command MUST exit with a stable partial-failure status, and running it again MUST
  finish the job. A recorded resource that no longer exists MUST count as already released.
- **FR-043**: `down` in a worktree that is not provisioned MUST succeed and change nothing.
- **FR-044**: Teardown MUST depend only on the registry. It MUST work when the worktree
  directory, its `wtenv.toml`, and its env file no longer exist.

**Garbage collection (`gc`)**

- **FR-045**: `gc` MUST examine every registry entry, across all repositories, and MUST
  treat an entry as orphaned only when all three of these hold:
  1. The repository recorded for the entry is still present on disk and answers git
     commands.
  2. Git in that repository no longer has the worktree: it is not listed at the recorded
     path, and it has not been moved to another path.
  3. Nothing exists at the recorded worktree path.

  `gc` MUST release each orphaned entry as `down` would.
- **FR-046**: `gc` MUST NOT touch anything that belongs to a worktree that still exists or
  to an unverifiable entry, except as FR-073 allows.
- **FR-047**: After a successful `gc`, the registry MUST contain no orphaned entry, other
  than entries skipped as busy under FR-077. Unverifiable entries MUST remain in the
  registry with all their resources.
- **FR-072**: An entry whose worktree cannot be found but which fails any check in FR-045
  MUST be kept and reported as `unverifiable`, with a stable reason that says which check
  failed: repository not found, git still lists the worktree, the worktree was moved, or
  the path still exists. Keeping an unverifiable entry is not a failure and MUST NOT change
  the exit status of `gc`.
- **FR-073**: `gc` MUST release an unverifiable entry only when its recorded worktree path
  is named explicitly on the command line (the option name is set during planning). `gc`
  MUST refuse a named path at which a worktree still exists, and MUST refuse an entry whose
  worktree was moved and still exists at another location. This form MUST support
  `--dry-run` and MUST report what it removed in the same way as a plain `gc`.
- **FR-074**: `gc` MUST repeat the FR-045 checks for each entry immediately before
  releasing it, while holding that entry's worktree lock (FR-076), and MUST skip the entry
  if they no longer hold.
- **FR-075**: A plain `gc` MUST delete, and `gc --dry-run` MUST only list. The behaviour of
  `gc` MUST NOT depend on whether or how often it has been run before. wtenv MUST NOT run
  `git worktree prune` or any other git command that changes the repository.

**Listing (`ls`)**

- **FR-048**: `ls` MUST list every worktree in the registry, across all repositories, and,
  when run inside a repository, that repository's worktrees that are not yet provisioned.
  For each it MUST show the repository, the worktree path, the port block with each
  variable's port, the database, the compose project, and the status.
- **FR-049**: The status MUST be one of: `provisioned`, `unprovisioned`, `incomplete` (an
  `up` or `down` was interrupted or partly failed), `orphaned`, or `unverifiable`. An
  unverifiable entry MUST be shown with its reason (FR-072).
- **FR-050**: `ls` MUST NOT change anything.

**Auto-provisioning and `exec`**

- **FR-051**: wtenv MUST offer an explicit, opt-in action that installs a git hook which
  runs `up` when a worktree is created. The hook MUST NOT be installed as a side effect of
  any other command, MUST do nothing on other checkouts, and MUST NOT run `down`, `gc`, or
  any other wtenv command.
- **FR-052**: A failure during auto-provisioning MUST NOT make the git operation fail. The
  error MUST be reported on standard error.
- **FR-053**: Installing MUST NOT overwrite or alter hook content that wtenv did not write.
  Uninstalling MUST remove only what wtenv added.
- **FR-054**: Git provides no event when a worktree is removed. Resources of worktrees
  removed through plain git MUST therefore be reclaimed by `gc`, and MUST show as `orphaned`
  in `ls` and `doctor` until then.
- **FR-055**: *Not delivered in v1.* The intended integration ran `up` when Claude Code
  created a worktree and `down` when Claude Code removed one. Planning found that Claude
  Code's worktree lifecycle hooks replace its own worktree creation and removal instead of
  notifying, and that Claude Code switches git hooks off when it creates a worktree
  (`research.md`, section 2). The integration is therefore dropped, as the "Claude Code
  hooks" assumption allows, and its alternatives are recorded in `docs/roadmap.md`. In v1 a
  worktree that Claude Code creates is provisioned by running `wtenv up` in it, and is
  reclaimed by `gc` after Claude Code removes it. v1 has no automatic `down`.
- **FR-056**: `exec -- <command>` MUST run the command with the worktree's wtenv-managed
  variables added to the current environment, MUST pass standard input, output, and error
  through unchanged, and MUST exit with the command's exit status.
- **FR-057**: `exec` in a worktree that is not provisioned MUST fail with a stable error and
  MUST NOT run the command.

**Diagnostics and the agent contract**

- **FR-058**: Every command MUST support `--json`. With `--json`, standard output MUST
  contain exactly one JSON document and everything else MUST go to standard error. For
  `exec`, `--json` applies only to wtenv's own errors; the command's output is not altered.
- **FR-059**: Every failure MUST carry a stable error code in JSON output and a stable exit
  status. At least these categories MUST be distinguishable: usage error; configuration
  error; not inside a worktree; worktree not provisioned; no free block; env file unusable;
  required dependency unavailable; template missing (`template_missing`); template in use
  (`template_in_use`); ownership conflict; post-up command failed; partial failure;
  registry busy; worktree busy; registry unreadable; and `doctor` found problems. The two
  template codes are fixed here. All other code values, and every exit status, are assigned
  during planning. None of them change afterwards.
- **FR-060**: `doctor` MUST check and report, without changing anything: (a) blocks in the
  registry that overlap; (b) allocated ports held by a process outside the owning worktree,
  with ports whose holder cannot be determined reported as in use but not as a conflict;
  (c) stale registry entries, meaning orphaned worktrees, unverifiable worktrees (each with
  its reason), and recorded resources that no longer exist; (d) incomplete entries;
  (e) Docker or Postgres that the current repository's configuration needs but that is
  unavailable. A dependency the configuration does not need MUST be reported as not
  required, not as a problem.
- **FR-061**: `doctor` MUST exit with status 0 when it finds no problems and with a stable
  "problems found" status otherwise. Each finding MUST carry a stable finding code.

**Configuration**

- **FR-062**: Configuration is an optional `wtenv.toml` in the root of the worktree. Each
  command MUST read it from the worktree in which it runs.
- **FR-063**: `wtenv.toml` MUST support exactly these settings: port variable names; block
  size; env file path; database type (Postgres or SQLite), template, and URL pattern;
  compose file path; and post-up commands.
- **FR-064**: An invalid value or an unknown setting MUST be a configuration error that
  names the setting. wtenv MUST NOT change anything when the configuration is invalid.
- **FR-065**: When `wtenv.toml` changes after provisioning, the next `up` MUST apply the
  change. A changed variable list MUST be applied within the existing block. A changed
  block size MUST result in a new block being allocated, the old one released, and the
  change reported. A configuration change MUST never cause an existing database to be
  removed or re-created.

**State and safety**

- **FR-066**: One registry per user, stored on the machine outside any repository, MUST be
  the single source of truth for what wtenv has allocated and created.
- **FR-067**: Every resource MUST be recorded in the registry in such a way that no
  interruption can leave a resource that wtenv created but did not record.
- **FR-068**: The registry MUST stay correct when several commands use it at once, and
  every change to it MUST be all-or-nothing. Every command MUST take the registry lock, one
  exclusive lock per user, for each read or read-check-write of the registry, and MUST
  release it before any slow step: copying a database, calling Docker, running a post-up
  command, or waiting for a worktree lock. A command that cannot get the registry lock
  within a bounded time MUST fail with the stable registry-busy error. Read-only commands
  MUST NOT be held up by another worktree's long-running step, such as a database copy.
- **FR-076**: `up` and `down` MUST hold a lock that belongs to the one worktree they act on,
  from start to finish, including the database copy and post-up commands. `gc` MUST hold
  the worktree lock of each entry while it re-checks and releases that entry. `ls`,
  `doctor`, `exec`, and every `--dry-run` run MUST NOT take a worktree lock.
- **FR-077**: An `up` or `down` that finds its worktree lock held MUST wait for it and then
  run in full. If it cannot get the lock within a bounded time, it MUST fail with the
  stable worktree-busy error and change nothing. `gc` MUST NOT wait for a worktree lock: it
  MUST skip that entry and report it as busy, which is not a failure and does not change
  its exit status.
- **FR-078**: The two wait bounds are set during planning and do not depend on the
  operation in progress. A lock held by a process that has ended MUST NOT block any later
  command.
- **FR-069**: After an interruption at any point, running `up` again MUST complete the
  provisioning and running `down` MUST remove what was created. Recovery MUST NOT need
  manual repair.
- **FR-070**: When the registry cannot be read, wtenv MUST stop with a stable error. It
  MUST NOT guess its contents and MUST NOT delete anything.
- **FR-071**: wtenv MUST NOT make network calls other than to the local services the user
  configured, and MUST NOT send telemetry, analytics, crash reports, or update checks.

### Non-Functional Requirements

- **NFR-001 Startup budget**: `wtenv --version` and `wtenv ls --json` MUST each finish in
  under 300 ms when warm. Measurement method:
  - Tool: `hyperfine`, on the maintainer's development machine.
  - Target: the installed `wtenv` executable, called directly, not through `uv run` or any
    other wrapper.
  - Setup for `ls --json`: run from the root of a git repository that has only its main
    worktree, with an empty registry.
  - Procedure: `hyperfine --warmup 5 --runs 30 '<command>'` for each of the two commands.
  - Pass: the reported mean is under 300 ms for each command.
  - Record: the result is recorded with the machine model, OS version, and wtenv version,
    and is re-measured before each release.
- **NFR-002 Provisioning time**: `up` MUST finish in under 5 seconds of wtenv's own work.
  Time spent copying the database and running the developer's post-up commands is excluded.
  Measurement method, on the sample app: (a) a first `up` in a new worktree with database
  isolation and post-up commands switched off, and (b) a repeat `up` in a fully provisioned
  worktree with the full configuration and post-up commands switched off. Both MUST take
  under 5 seconds of wall-clock time.
- **NFR-003 Coverage**: the constitution's rule of at least 80% test coverage for core
  modules applies to these eight core areas, each measured on its own (not averaged):
  1. Registry: stored state, concurrent access, all-or-nothing writes, recovery.
  2. Port allocation.
  3. Worktree identity.
  4. Config parsing and validation.
  5. Database provisioning: Postgres and SQLite creation, the ownership check, removal.
  6. Compose override generation.
  7. Garbage collection: finding orphans and deciding what is safe to remove.
  8. Env-file writing: wtenv's section, and preserving the developer's lines.

  Coverage means line coverage over the whole test suite (unit and integration), taken
  from a run where Docker and Postgres are available so that no test is skipped. The plan
  MUST map each area to the code that implements it. Code outside these areas is still
  tested but is not held to the threshold.
- **NFR-004 Platforms**: all behaviour in this specification MUST work on macOS and Linux.
  On Windows it is supported only inside WSL2.

### Key Entities

- **Worktree**: a git worktree, identified by its git directory. Its location is recorded.
  Has a status: provisioned, unprovisioned, incomplete, orphaned, or unverifiable.
- **Registry**: the per-user record of every worktree wtenv has provisioned and every
  resource it created for each. The single source of truth for teardown.
- **Port block**: a contiguous set of ports reserved for one worktree, with the mapping of
  each variable name to a port.
- **Env file**: the file in a worktree that carries its variables. wtenv owns one section
  of it, between its begin and end marker lines; the developer owns the rest.
- **Worktree database**: a Postgres database or SQLite file created for one worktree from a
  template. Belongs to exactly one worktree.
- **Compose project**: the project name and generated override that keep one worktree's
  compose stack apart from the others. The stack's containers, networks, and volumes belong
  to it.
- **Configuration**: the optional `wtenv.toml` in a worktree, holding the settings listed in
  FR-063.
- **Auto-provisioning hook**: the opt-in git hook that runs `wtenv up` when a worktree is
  created.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On one laptop, 5 worktrees of the sample app (a FastAPI server, Postgres, and
  a docker-compose stack) all run their API server and database at the same time, with zero
  manual edits to any file.
- **SC-002**: Provisioning a worktree takes under 5 seconds, not counting the copy of a
  small database (measured as in NFR-002).
- **SC-003**: After all worktrees of the sample app are removed with `git worktree remove`
  and `gc` is run once, zero orphaned databases, containers, volumes, or registry entries
  remain.
- **SC-004**: `wtenv --version` and `wtenv ls --json` each respond in under 300 ms
  (measured as in NFR-001).
- **SC-005**: When 5 worktrees are provisioned at the same moment, no two receive the same
  port, in 20 out of 20 repeated trials.
- **SC-006**: Provisioning an already provisioned worktree again yields the same ports, the
  same database, and a byte-identical env file, every time.
- **SC-007**: Across all cleanup tests, including ones that plant databases, containers,
  volumes, and files with wtenv-style names, wtenv removes zero items that it did not
  create and record.
- **SC-008**: Every command completes without waiting for input and produces output an
  agent can parse, so that an agent can provision, use, inspect, and tear down a worktree
  with no human involved.
- **SC-009**: A developer with no configuration file gets working port isolation in a new
  worktree with a single command.

## Assumptions

- **Shared Postgres server**: Postgres isolation targets one Postgres server on the local
  machine that all worktrees share, whether it runs natively or in a long-lived container.
  Each worktree gets its own database on that server. A Postgres service defined inside the
  per-worktree compose file is isolated by compose isolation instead (own container, own
  volume, own port) and is not copied from a template.
- **Sample app**: the acceptance fixture is a FastAPI app that reads `PORT` and
  `DATABASE_URL`, a template database on the shared local Postgres server, and a compose
  file with at least one service that publishes a host port and one named volume.
- **Compose resources count as wtenv-created**: containers, networks, and volumes that
  belong to a compose project wtenv created and recorded are treated as created by wtenv,
  even though the developer's own `docker compose up` started them. This is how the
  constitution's "created AND recorded" rule is applied to compose (Principle II, as
  amended in constitution v1.0.1).
- **`up` generates, it does not start**: `up` prepares the compose project but does not
  start it. A developer who wants it started adds a post-up command.
- **`down` removes the database**: the worktree's database and its data are deleted by
  `down`, and connections still open to it are closed. `--dry-run` shows this in advance.
- **Removal automation**: git has no hook for worktree removal, so removed worktrees are
  cleaned up by running `gc`. v1 has no automatic `down`.
- **Claude Code hooks**: Claude Code was understood to expose worktree creation and removal
  hooks, with their exact behaviour to be verified during planning, and the integration to
  be dropped from v1 if they turned out to be unusable. Planning verified them
  (`research.md`, section 2): they replace Claude Code's own worktree handling, so the
  integration is dropped and nothing else in this spec changes.
- **Block stability**: a block is stable from `up` until `down` or `gc`. A worktree that is
  torn down and provisioned again may receive a different block.
- **Identity follows the git directory**: a linked worktree that is moved or renamed keeps
  its git directory, so it stays the same worktree and keeps its resources. A repository
  that is moved or renamed gets new git directories, so its worktrees are new worktrees to
  wtenv; their old entries are unverifiable, not orphaned, and their databases are kept
  until the developer names them to `gc`. A worktree on a removable or network volume that
  is not mounted when `gc` runs is also unverifiable.
- **Hand-deleted worktrees need `git worktree prune`**: a worktree directory deleted
  without git is reclaimed by `gc` only after the developer runs `git worktree prune`,
  which is the developer's own statement that the worktree is gone.
- **Scope of `down`**: `down` acts on the worktree it is run in. Worktrees that no longer
  exist are handled by `gc`.
- **Scope of `ls` and `gc`**: both cover every repository in the registry, with no flag to
  narrow them.
- **`exec` variables**: `exec` sets the variables wtenv manages. It does not load the
  developer's own lines from the env file.
- **Post-up commands are safe to repeat**: they run on every `up`, so the developer keeps
  them idempotent (as migration commands normally are).
- **Refreshing from the template**: changes to the template after a worktree's database was
  created are not carried over. To get a fresh copy, run `down` and then `up`.
- **Configuration is trusted**: `wtenv.toml`, including its post-up commands, is trusted in
  the same way as any other script committed to the repository.
- **One user per machine**: the registry is per user. Two user accounts sharing one machine
  are not coordinated with each other.
- **Dependencies**: git is installed. Docker is needed only when compose isolation is
  configured, and a local Postgres server only when Postgres isolation is configured.
- **No interactive mode in v1**: because no command prompts, v1 has no `--interactive` flag.

### Out of Scope (v1)

- Cloud preview environments.
- Kubernetes.
- MySQL, MongoDB, and any database other than Postgres and SQLite.
- Native Windows (WSL2 only).
- Any graphical interface.
- Team or server sync of the registry.
- Paid features.
- A daemon or any background process.
