# Feature Specification: Compose dev setups — repository-owned overrides, port variables, and compose-hosted databases

**Feature Branch**: `002-compose-dev-setup`

**Created**: 2026-10-09

**Status**: Draft

**Input**: User description: "Make wtenv work with the most common Compose dev setup, found by
running v0.1.0 on fastapi/full-stack-fastapi-template." The full description (dogfood evidence,
three prioritised user stories, constraints on existing guards and compatibility, the acceptance
target, and the out-of-scope list) is reflected in the sections below.

## Overview

Running wtenv v0.1.0 on `fastapi/full-stack-fastapi-template`, a widely copied FastAPI +
Postgres + Compose starter, showed three gaps. All three were reproduced.

1. **The repository's own override file blocks `up`.** The template tracks its own Compose
   override file, which holds the dev ports, the dev command, and the `watch` configuration.
   Compose loads only one override file, and wtenv did not create this one, so `wtenv up` exits
   with `ownership_conflict` (exit 11). The repository's normal dev flow cannot be used with
   wtenv at all.
2. **Remapped ports cannot be found.** `up` and `ls` show that, for example, the `backend`
   service's container port 8000 is published on host port 20002, but no variable in the env
   file or in `exec` carries that number. An agent cannot find its own backend.
3. **The database runs as a Compose service.** The template's Postgres is the `db` service of
   the stack, not a Postgres server on the host. `[database]` can only describe a host Postgres
   server or a SQLite file. The tracked `.env` keeps `localhost:5432` for host-side tools, so in
   every worktree a host-side `DATABASE_URL` points at the wrong database.

This feature closes those gaps without weakening any guard that limits what wtenv may change or
delete.

**Terms used in this document**

- **Spec 001**: `specs/001-worktree-runtime-isolation/spec.md`. Its requirements are cited as
  `001:FR-nnn`. Requirements in this document are cited as `FR-nnn`.
- **Override file**: a Compose file with one of the names Compose loads automatically beside
  the compose file (001: files.md, Names).
- **Repository-owned override**: an override file that wtenv did not create and has not
  recorded, whether git tracks it or not.
- **Isolation settings**: what wtenv generates for a worktree so that its Compose stack gets
  the worktree's project name and every host-published port is moved into the worktree's
  block. In v0.1.x they are the generated override file (001:FR-029).
- **Published port**: one mapping of a service's container port (with its protocol) to a host
  port.
- **Port variable**: a variable that carries the host port wtenv assigned to one published port
  (User Story 2).
- **Compose-hosted database**: a database server that runs as a service of the worktree's own
  Compose project, as opposed to a shared Postgres server on the host (001:FR-020) or a SQLite
  file (001:FR-021).
- **Host-side tool**: a process that runs on the host rather than in a container, such as a test
  run, a migration, a script, or an agent.

## Clarifications

### Session 2026-10-10

- Q: 001:FR-030 promises that plain Compose commands with no extra flags are isolated, but
  Compose loads only one override file automatically and a repository like the fastapi template
  tracks its `.env`. In a worktree whose repository owns its override, how do Compose commands
  get isolation? → A: Isolation applies to Compose commands run through `wtenv exec`, and `up`
  says so in text and JSON. A plain `docker compose` command in such a worktree runs without
  isolation, and the documentation says so plainly. 001:FR-030 is relaxed only for these
  worktrees (FR-004, FR-033).
- Q: When the repository's override publishes a fixed host port for a service, whose value
  wins? → A: wtenv's remapped port wins for every published port, and every other setting in the
  repository's override is kept (FR-003).
- Q: What does `up` do when the compose-hosted database keeps its data somewhere that is not
  specific to the worktree (a fixed-name volume, an external volume, or a host directory outside
  the worktree)? → A: `up` refuses with a stable error code before changing anything (FR-027).
- Q: In a worktree whose repository owns its override, do post-up commands (001:FR-036) get the
  same isolation as `wtenv exec`? → A: Yes. Otherwise a post-up command such as
  `docker compose up -d` would start an unisolated stack on the repository's fixed ports
  (FR-033).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A repository that owns its override file can use wtenv (Priority: P1)

A developer whose repository ships its own override file enables compose isolation. `wtenv up`
succeeds. The repository's override keeps taking effect, with its dev command, `watch`
configuration, environment, volumes, and extra services. For Compose commands run through
`wtenv exec`, wtenv still gives the worktree its own project name, moves every published port
into the worktree's block, and keeps its volumes apart. A plain `docker compose` command run
without `wtenv exec` gets no isolation in such a worktree, and `up` says so. wtenv never
overwrites, deletes, or silently ignores the repository's file.

**Why this priority**: it is a blocker. Without it, a repository like the fastapi template
cannot use wtenv's compose isolation at all, and the other two stories have nothing to run on.

**Independent Test**: in a repository whose compose file publishes no ports and whose
repository-owned override publishes them and sets a dev command, configure compose isolation,
run `wtenv up` in two worktrees, and start each stack through `wtenv exec`. Both stacks run at
the same time on ports inside their own blocks, each runs the override's dev command, and the
override file is byte-identical to the committed version throughout.

**Acceptance Scenarios**:

1. **Given** compose isolation is configured and the repository has a repository-owned
   override, **When** `wtenv up` is run, **Then** it succeeds, the override file is
   byte-identical to what it was before, and `git status` shows no change.
2. **Given** a worktree provisioned as in scenario 1, **When** the developer starts the stack
   through `wtenv exec` (FR-004), **Then** the stack uses the worktree's project name, every
   published port, including those that only the repository's override declares, is on a port
   of the worktree's block, and every other setting of the override takes effect.
3. **Given** three worktrees provisioned as in scenario 1, **When** their stacks run at the
   same time, **Then** no two share a host port, a container, a network, or a project-scoped
   volume.
4. **Given** a provisioned worktree, **When** `wtenv up` is run again with nothing changed,
   **Then** the ports, the project name, and the isolation settings are identical to the
   previous run.
5. **Given** a provisioned worktree with a repository-owned override, **When** `wtenv down` or
   `wtenv down --dry-run` is run, **Then** the override is never listed as an item to remove,
   is byte-identical afterwards, and everything else is released as 001:FR-038 describes.
6. **Given** a repository with no override file of its own (and none of the setups FR-028
   refuses), **When** `wtenv up` is run, **Then** wtenv behaves exactly as v0.1.1 does: the same generated override, under the same
   name, with the same bytes, and plain `docker compose` commands with no extra flags use it.
7. **Given** the repository has two override files with names Compose loads automatically,
   **When** `wtenv up` is run, **Then** it fails with a stable error naming both files and
   changes nothing, because Compose would silently ignore one of them.
8. **Given** the repository's override publishes service `backend` on fixed host port 8000 and
   sets its dev command, **When** the stack is started through `wtenv exec`, **Then** `backend`
   is published on a port of the worktree's block, not on 8000, and runs the override's dev
   command (FR-003).
9. **Given** a worktree provisioned as in scenario 1, **When** `wtenv up` is run, with or
   without `--json`, **Then** the output says that Compose commands are isolated only when run
   through `wtenv exec`, and that a plain `docker compose` command runs without isolation.
10. **Given** a worktree provisioned as in scenario 1 whose isolation settings `exec` cannot
    apply (for example they were deleted, or `COMPOSE_PROJECT_NAME` is set in the calling
    environment), **When** any command is run through `wtenv exec`, **Then** `exec` fails with
    a stable error naming the cause and does not run the command (FR-033).

---

### User Story 2 - Remapped published ports are available as named variables (Priority: P2)

After `wtenv up`, every published port of the worktree's Compose stack is available as a
variable, in the env file and in `wtenv exec`. The name follows one documented rule built from
the service name, the container port, and the protocol, so it is the same in every worktree,
and only its value differs. An agent can find "the backend's port in this worktree" without
parsing `ls` output.

**Why this priority**: without it, a stack that runs (Story 1) cannot be reached by the agents
working in it. It comes after Story 1 because the fastapi template needs Story 1 to provision
at all, but it also works on its own for any repository that v0.1.x already supports.

**Independent Test**: with a compose file whose `backend` service publishes container port 8000
and whose `mailpit` service publishes 8025 and 1025, run `wtenv up` in two worktrees. In each,
the env file and `wtenv exec -- env` carry one variable per published port; the names are the
same in both worktrees, the values are each worktree's remapped ports, and a request to the
backend's variable reaches that worktree's backend.

**Acceptance Scenarios**:

1. **Given** compose isolation is configured and service `backend` publishes container port
   8000, **When** `wtenv up` is run, **Then** the env file contains a variable whose name the
   documented rule derives from `backend`, `8000`, and the protocol, set to the host port
   assigned to it, and `wtenv exec` sets the same variable to the same value.
2. **Given** a published port that is tied to a configured port variable (001:FR-031), **When**
   `wtenv up` is run, **Then** it also gets its rule-derived variable, carrying the same port
   number.
3. **Given** two worktrees of the same repository, **When** `wtenv up` is run in both, **Then**
   the set of variable names is identical in both and the values differ.
4. **Given** a provisioned worktree, **When** `wtenv up` is run again with nothing changed,
   **Then** the env file is byte-identical (001:FR-017).
5. **Given** a published port is removed from the compose setup, **When** `wtenv up` is run
   again, **Then** its variable is no longer in the env file or in `exec`.
6. **Given** two published ports for which the rule would give the same name, or a
   rule-derived name equal to a variable the configuration already defines, **When**
   `wtenv up` is run, **Then** it fails with a stable error naming both sources and changes
   nothing.
7. **Given** a provisioned worktree, **When** `wtenv up` or `wtenv ls` is run, with or without
   `--json`, **Then** each published port is shown with its variable name next to its host
   port.

---

### User Story 3 - A database that runs as a Compose service gets a per-worktree connection setting (Priority: P3)

A developer whose database is a service of the Compose stack tells wtenv which service it is.
After `wtenv up`, the worktree's env file and `wtenv exec` carry the database's host port and a
database URL that points at that worktree's own database service on its remapped port. Host-side
tools that use the URL reach their own worktree's database. Each worktree's database data lives
in its own project's storage, and `down` and `gc` release it with the project, under the same
ownership rules as every other compose resource.

**Why this priority**: it fixes the "every worktree's host-side tools hit the wrong database"
problem for the most common Compose setup. It comes last because it builds on Story 1 (the
stack must run) and Story 2 (the port must be known).

**Independent Test**: in three worktrees of a repository whose `db` service runs Postgres and
stores its data in a named volume, configure the database as that compose service, run
`wtenv up`, and start each `db`. Through `wtenv exec`, write a row in worktree 1; it is visible
in worktree 1 and absent in worktrees 2 and 3. Run `wtenv down` in worktree 1; its database
container and data volume are gone and the other two are untouched.

**Acceptance Scenarios**:

1. **Given** the configuration names the compose service that hosts the database, **When**
   `wtenv up` is run, **Then** the env file and `exec` carry the database URL (default
   `DATABASE_URL`, as 001:FR-020) built from the configured URL pattern with the host port
   that service's database port was given, and the port itself under its Story 2 variable; no
   host Postgres server is contacted and no template is copied.
2. **Given** three worktrees provisioned as in scenario 1 with their stacks running, **When** a
   host-side tool run through `wtenv exec` in each writes to its database, **Then** each write
   is visible only in its own worktree's database.
3. **Given** the repository's tracked `.env` sets a database URL with `localhost:5432`, **When**
   a host-side tool is run through `wtenv exec`, **Then** the tool's environment carries the
   worktree's URL, not the tracked one, and the tracked `.env` is not changed.
4. **Given** a provisioned worktree whose database holds data, **When** `wtenv up` is run again,
   **Then** the port, the URL, and the data are unchanged.
5. **Given** a provisioned worktree, **When** `wtenv down --dry-run` and then `wtenv down` are
   run, **Then** the dry run lists the database service's container and its project-scoped data
   volume, and the real run removes them under 001:FR-039; nothing outside the worktree's
   recorded project is touched.
6. **Given** the configuration names a service that does not exist, publishes no port for the
   database, or publishes more than one, **When** `wtenv up` is run, **Then** it fails with a
   configuration error naming the service and changes nothing.
7. **Given** the database service stores its data in a volume with a fixed name, an external
   volume, or a host directory outside the worktree, **When** `wtenv up` is run, **Then** it
   fails with a stable error naming the service and that storage, and changes nothing (FR-027).
8. **Given** a `wtenv.toml` that uses the v0.1.x database settings (host Postgres or SQLite),
   **When** `wtenv up` is run, **Then** behaviour is exactly as in v0.1.1, except for the setups
   FR-028 refuses.

---

### Edge Cases

- **The repository's override is edited, or a branch switch changes it, after provisioning**:
  the next `up` recomputes the isolation settings from the current files. Ports keep the
  deterministic assignment of 001 (data-model, "Assigning ports within the block"), so a port
  that still exists keeps its number when the set of published ports is unchanged.
- **A repository-owned override appears after provisioning, under a different override name
  from wtenv's generated one** (for example a pull adds one): Compose would see two override
  files. The next `up` stops using the auto-loaded name for its own settings, removes its own
  generated file under the 001:FR-087 header check, and honours the repository's file. It never
  touches the repository's file.
- **A repository-owned override appears at the same path as wtenv's recorded override** (a
  checkout replaced wtenv's ignored file with the repository's tracked one): the file lacks
  wtenv's header, so wtenv never rewrites or deletes it (001:FR-087 guard unchanged). When git
  tracks the file, `up` treats it as repository-owned and stops recording it as its own (FR-006);
  `down` reports it as kept, not as failed. When git does not track it, `up` fails with
  `ownership_conflict` exactly as 001:FR-087 says.
- **A repository-owned override disappears** (deleted, or a branch without it is checked out):
  the next `up` returns to the v0.1.x arrangement for that worktree (FR-008). Until then, `exec`
  fails as FR-033 says.
- **The repository's override is a symbolic link**: a link at an override name that wtenv has
  not recorded is the repository's own override, whether git tracks it or not. It is kept, and
  never written, deleted, excluded, or followed for writing; wtenv only reads it through Compose,
  so 001:FR-086 does not apply to it. A symbolic link at any path wtenv has recorded as its own,
  or at the isolation file's path, still fails closed, and no header is read through it (FR-009).
- **The repository's override publishes a host port range onto one container port**:
  `unsupported`, naming the service, as for the compose file itself (001:FR-033).
- **The same container port of one service is published twice with the same protocol** (for
  example on two host addresses): the Story 2 rule cannot give both a name; `up` fails with a
  stable error naming the service and changes nothing.
- **Service names that differ only in case or punctuation** (`my-api` and `my_api`): FR-012
  applies; `up` either gives them different names or refuses, never one name for two ports.
- **A developer's own line outside wtenv's section defines a Story 2 or Story 3 variable**:
  wtenv leaves it alone and warns (001:FR-080).
- **The compose file interpolates one of wtenv's host-side variables into a container** (for
  example `${DATABASE_URL}` passed to a service): when Compose is run through `wtenv exec`, the
  container receives the host-side value, which names `localhost` and the remapped port. wtenv
  does not change what containers receive; the documentation says so (FR-025).
- **The database service's data lives in an anonymous volume** (the service declares no volume
  and the image declares one): the data is per-worktree, but `down` and `gc` keep anonymous
  volumes (001 reading R6). `up` warns that the database's data will be kept on `down`.
- **The database service's data lives in a host directory inside the worktree** (for example
  `./data`): the data is per-worktree, but wtenv did not create the directory, so `down` and
  `gc` keep it. `up` accepts it and warns (FR-027).
- **The database service's data lives in a fixed-name volume, an external volume, or a host
  directory outside the worktree**: every worktree, and possibly the main checkout, would share
  one data store. `up` refuses before changing anything (FR-027).
- **A Compose command run through `wtenv exec` passes its own project name or compose files**
  (`-p`, `-f`, `--env-file`, or similar): the command's own choice wins, so it is not isolated. Several
  compose files and `-f` arguments are out of scope.
- **The database service is not running when a host-side tool uses the URL**: the tool fails to
  connect. `up` does not start containers (001:FR-034); `doctor` is unchanged by this feature.
- **`COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` is set by the developer** in the environment or in
  `.env`: `up` fails with `unsupported` as in v0.1.x, whether or not the repository owns its
  override. In a worktree whose repository owns its override, `exec` also fails when either is
  set in the environment it is called from or in `.env` beside the compose file, or when
  `COMPOSE_ENV_FILES` is set in that environment (FR-033). In any worktree, `exec` fails when an
  inherited `COMPOSE_FILE` names another worktree's isolation settings (FR-033), and so does
  `up`, before its first post-up command runs.
- **The worktree's block is too small** for the published ports of the compose file and the
  repository's override together: `config_invalid` with the smallest block size that fits
  (001:FR-032 unchanged).

## Requirements *(mandatory)*

### Functional Requirements

**Repository-owned override files (User Story 1)**

- **FR-001**: When compose isolation is configured and the worktree has a repository-owned
  override, `up` MUST succeed instead of failing with `ownership_conflict`, provided every other
  check passes.
- **FR-002**: wtenv MUST NOT write, rename, move, delete, or change the permissions of a
  repository-owned override, in `up`, `down`, `gc`, or any dry run, and MUST NOT add it to
  `.git/info/exclude`. It MUST NOT appear as an item to remove in any output. When git ignores
  an untracked repository-owned override because of a line of wtenv's exclude block (a line
  written for another worktree of the same repository), `up` MUST warn, because a checkout or
  `git clean -X` can then overwrite or delete the file.
- **FR-003**: The stack started through `wtenv exec` (FR-004) MUST include every setting of the
  repository-owned override, except the host port of each published port and the project name,
  which come from wtenv's isolation settings. Port remapping MUST cover every published port of
  the combined compose file and repository-owned override, and 001:FR-033 ("never silently
  un-remapped") MUST hold for that combined result. When the repository's override publishes a
  fixed host port, or a host port taken from a variable, wtenv's remapped port MUST win, and
  every other setting in the override MUST be kept, including the rest of that published-port
  entry (its host address and protocol).
- **FR-004**: In a worktree whose repository owns its override, Compose commands MUST be isolated
  when they are run through `wtenv exec` (FR-033), which applies both the repository's override
  and wtenv's isolation settings. A plain `docker compose` command run without `wtenv exec` in
  such a worktree runs the repository's setup without isolation: it gets neither the worktree's
  project name nor its remapped ports. `up` MUST say this in text and JSON (FR-005), and the user
  documentation MUST state it plainly, including that a plain command runs without isolation.
  001:FR-030 is relaxed only for these worktrees; it holds unchanged for every other worktree
  (FR-008).
- **FR-005**: `up` MUST report, in text and JSON, when a worktree uses a repository-owned
  override, and MUST state how Compose commands pick up isolation in that worktree.
- **FR-006**: When a recorded override path now holds a file that git tracks, whether or not it
  starts with wtenv's header, wtenv MUST never write or delete it. At an auto-loaded override
  name, `up` MUST treat it as a repository-owned override and stop recording it as wtenv's; at
  the isolation file's path, `up` MUST fail with `ownership_conflict`. `down` (also with
  `--dry-run`) MUST report it as kept, not failed, and drop its record without touching the
  file. `gc` and `gc --release` act on a path that is no longer a git worktree: `gc` finds the
  directory already removed, so nothing is failed or kept and the record is dropped; in
  `gc --release` git cannot say whether a file is tracked, so a header-less file at the recorded
  path is left alone and reported under `failed` (001:FR-087), and a file that still carries
  wtenv's header is removed as in v0.1.1. In every other case 001:FR-087 applies unchanged.
- **FR-007**: When more than one override file with a name Compose loads automatically exists
  beside the compose file: if exactly one of them is repository-owned and every other one is
  wtenv's own recorded file, `up` MUST remove wtenv's files under the 001:FR-087 header check and
  continue with the repository's file; if two or more are repository-owned, `up` MUST fail with
  a stable error naming them and MUST change nothing.
- **FR-008**: For a worktree with no repository-owned override, `up` MUST generate the same
  override file, under the same name and with the same bytes, as v0.1.1 for the same inputs,
  and 001:FR-030 MUST hold unchanged. The only exception is the setups FR-028 refuses.
- **FR-009**: Everything wtenv generates for a worktree's isolation settings, wherever it lives,
  MUST carry the same protections as the v0.1.x generated override: recorded before or as it is
  created (001:FR-067), excluded from `git status` (001:FR-018), never written or deleted
  through a symbolic link, with no header read through one (001:FR-086), rewritten or deleted only while it still carries wtenv's
  header (001:FR-087), removed by `down` and `gc` only when its recorded path has a form wtenv
  records (001 cli.md, `wtenv down`, "Recorded values").
- **FR-033**: In a worktree whose repository owns its override, `exec` MUST apply the worktree's
  isolation settings, together with the repository's override, to every Compose command that
  the command it runs starts, directly or through a child process such as a script or a `make`
  target. Post-up commands (001:FR-036) MUST get the same. `exec` MUST fail with a stable error
  code and exit status (FR-031), naming the cause, and MUST NOT run the command, when it
  cannot apply them: the worktree's recorded isolation settings are missing, no longer start
  with wtenv's header, or are reached through a symbolic link; the repository-owned override
  recorded for the worktree is gone, or another override file with a name Compose loads
  automatically has appeared beside the compose file; `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` or
  `COMPOSE_ENV_FILES` is set in the environment `exec` is called from; or `COMPOSE_PROJECT_NAME`
  or `COMPOSE_FILE` is set in the `.env` file beside the compose file, in any line form Compose
  accepts there (`KEY=value` or `KEY: value`, with or without `export`, after an optional
  byte-order mark; research R7). `exec` and post-up commands MUST make Compose read no `.env`
  file other than that one, so that a `.env` in the directory a Compose command runs from cannot
  set the project name. In every other worktree, `exec` behaves as in v0.1.1, with two
  exceptions, each reported with the same error code and without running the command: in any
  worktree, `exec` MUST fail when `COMPOSE_FILE` in the environment it is called from names
  another worktree's isolation settings; and in a worktree whose recorded compose arrangement
  is automatic, `exec` MUST fail when the override files beside the compose file no longer
  match the record, because a repository override has appeared since `up`: another override
  file with a name Compose loads automatically exists there, or the recorded override file is a
  symbolic link or no longer starts with wtenv's header. The first of those two checks also
  applies to post-up commands, in every worktree: when `COMPOSE_FILE` in the environment `up`
  is called from names another worktree's isolation settings, `up` MUST fail with the same
  error code, before the first post-up command runs, and run none of them. `up` leaves the
  provisioned resources in place and the entry `incomplete`, as when a post-up command fails
  (001:FR-037). The refusal runs no command, so its error is `isolation_unavailable` (exit 20),
  not `post_up_failed`; the entry becomes `incomplete` and the provisioned resources stay in
  place, as 001:FR-037 requires for a stopped post-up. The check runs at both places `up` runs
  post-up commands, including a repeat `up` with nothing changed. In a worktree with `[compose]`, `up` already fails with
  `unsupported` for any `COMPOSE_FILE` before it reaches post-up commands, so the check can
  only fire in a worktree without `[compose]`.

**Port variables for published ports (User Story 2)**

- **FR-010**: When compose isolation is configured, `up` MUST write one variable per published
  port of the worktree's Compose setup to wtenv's section of the env file, set to the host port
  assigned to it, and `exec` MUST set the same variables (001:FR-056). Ports tied to a
  configured variable (001:FR-031) MUST also get their variable.
- **FR-011**: The variable name MUST be derived only from the service name, the container port,
  and the protocol, by one rule that is documented in the user documentation. It MUST NOT depend
  on the block, the port assigned, the worktree, or the order of services, so the same published
  port has the same name in every worktree of a repository.
- **FR-012**: Two different published ports MUST never share a variable name. When the rule
  would give two published ports the same name, or give a published port the name of a variable
  the configuration defines (including the database URL variable), `up` MUST fail with a stable
  error that names both sources and MUST change nothing.
- **FR-013**: The variables MUST be written in a fixed order, so that 001:FR-017 (byte-identical
  env file on a repeat `up`) holds.
- **FR-014**: `up` and `ls`, in text and JSON, MUST show each published port's variable name
  together with its service, container port, and host port.
- **FR-015**: The variables MUST NOT take ports of their own: they name ports already assigned
  under 001:FR-031, so the block size needed is unchanged (001:FR-032).
- **FR-016**: When compose isolation is not configured, no such variables are written, and the
  env file is as in v0.1.1.

**Compose-hosted databases (User Story 3)**

- **FR-017**: The configuration MUST be able to say that the worktree's database is a named
  service of its Compose setup, as an alternative to the existing host Postgres and SQLite modes.
  A worktree has at most one database mode; configuring more than one MUST be a configuration
  error that names the settings.
- **FR-018**: For a compose-hosted database, the developer MUST be able to give a URL pattern
  from which `up` builds the database URL, with a placeholder for the host port that the
  database service's port was given. The host part of the URL MUST name the local machine
  (001:FR-025).
- **FR-019**: `up` MUST write the database URL (default variable `DATABASE_URL`, as 001:FR-020)
  to wtenv's section of the env file, and `exec` MUST set it. The host port MUST also be
  available under its Story 2 variable.
- **FR-020**: Credentials in the URL MUST be suppliable without committing them to `wtenv.toml`
  (001:FR-026), MUST NOT be stored in the registry, and MUST NOT appear in any wtenv output
  (001:FR-019).
- **FR-021**: The configured service MUST exist in the worktree's Compose setup and publish
  exactly one port that serves as the database port; otherwise `up` MUST fail with a
  configuration error naming the service and MUST change nothing.
- **FR-022**: `up` MUST NOT contact the database server for a compose-hosted database, MUST NOT
  copy a template, and MUST NOT start containers (001:FR-034).
- **FR-023**: Teardown of a compose-hosted database MUST rely only on the existing compose
  teardown: the service's containers and the project-scoped volumes that hold its data are
  removed by `down` and `gc` under 001:FR-039 and the `<project>_` prefix rule, and nothing else
  is removed for it. `--dry-run` MUST list them.
- **FR-024**: Running `up` again MUST keep the same port and URL and MUST NOT remove or reset the
  database's data (001:FR-023).
- **FR-025**: The user documentation MUST state that the port and URL variables are host-side
  values, and that a container that receives one of them through interpolation gets the
  host-side value.
- **FR-026**: When the database service keeps its data in an anonymous volume, `up` MUST warn
  that `down` and `gc` will keep that data (001 reading R6).
- **FR-027**: When the database service keeps its data somewhere that is not specific to the
  worktree's project — a volume with a fixed name (the 001 reading R8 rule), an external volume,
  a volume of the `nfs`, `cifs` or local bind driver whose device is not an absolute path, or a
  host directory outside the worktree — `up` MUST fail with a stable error code (FR-031)
  that names the service and that storage, before changing anything. A host directory inside
  the worktree is specific to it: `up` MUST accept it and MUST warn, as for FR-026, that `down`
  and `gc` keep that data.

**Compatibility and safety (all stories)**

- **FR-028**: Every `wtenv.toml` accepted by v0.1.1 MUST be accepted unchanged and MUST produce
  the same ports, project name, database, and generated override, with one exception: a setup
  that the FR-011 rule cannot name without a collision is refused with `port_variable_conflict`
  and changes nothing (FR-012). There are exactly three such setups, all accepted by v0.1.1: one
  container port and protocol of one service published more than once (for example on two host
  addresses); two service names that the rule maps to one name (`my-api` and `my_api`, `API`
  and `api`); and a `ports` entry whose name equals a rule-derived name. For every other
  configuration, the only visible difference is the Story 2 variables added to wtenv's env-file
  section when compose isolation is on; the first `up` after upgrading reports the section as
  `updated`.
- **FR-029**: Registries written by v0.1.x MUST be read and used unchanged, with no migration
  step. Facts that the new modes need MUST be recorded only for worktrees that use them. If
  planning finds that a registry migration cannot be avoided, it MUST come back to the spec
  before any task is written.
- **FR-030**: These guards MUST stay exactly as spec 001 defines them, for every mode this
  feature adds: the compose project-name guard and the recorded-value checks (001:FR-088,
  001 cli.md "Recorded values"); the recorded-path guard for every file wtenv writes; the
  override header check (001:FR-087, except the outcome FR-006 adds for a git-tracked file,
  which still never writes or deletes it); the volume label and `<project>_` prefix rules
  (001:FR-039, reading R8); the symbolic-link rule (001:FR-086); the rule that a tracked env file
  is never written (001:FR-018); and "created and recorded" (constitution Principle II).
- **FR-031**: New error and warning conditions MUST use stable codes and exit statuses
  (constitution Principle IV). Existing codes and exit statuses MUST keep their meaning; new
  `details` keys and `reason` values MAY be added.
- **FR-032**: Every new behaviour MUST be reported in the `--json` output of the command that
  performs it (001:FR-058).

### Relationship to spec 001

By story:

- **Story 1** touches 001:FR-029, FR-038, FR-039 (where wtenv's settings live and what `down`
  removes); relaxes 001:FR-030, only for worktrees whose repository owns its override, where
  Compose commands are isolated only when run through `wtenv exec` (FR-004), and the outcome,
  not the guard, of 001:FR-087 (FR-006); extends 001:FR-031 to 001:FR-033 to the repository's
  override; extends 001:FR-036 and FR-056 so that post-up commands and `exec` apply the
  isolation settings in those worktrees, and relaxes 001:FR-056 and FR-057 there, because
  `exec` refuses every command when it cannot apply them (FR-033). It also relaxes 001:FR-056
  and FR-057 in every other worktree for two refusals only: an inherited `COMPOSE_FILE` that
  names another worktree's isolation settings, and, in the automatic arrangement, an override
  file that has appeared since `up` (FR-033). It extends 001:FR-036 in every worktree for one
  refusal: `up` does not run post-up commands when an inherited `COMPOSE_FILE` names another
  worktree's isolation settings (FR-033).
- **Story 2** touches 001:FR-015 to FR-017, FR-031, FR-048, FR-056 (new variables in the env
  file, `exec`, and output). It narrows 001:FR-031 for the three setups FR-028 refuses
  (FR-012), and relaxes nothing else.
- **Story 3** extends 001:FR-020 to FR-027 and FR-063 with a third database mode. It relaxes
  nothing; 001:FR-022 is met through the project rather than the database name.
- **Unchanged for all three**: 001:FR-018, FR-034, FR-039's volume rules, FR-067, FR-070,
  FR-086, FR-088, and every requirement not listed in the table below.

| Spec 001 requirement | Effect of this feature |
|----------------------|------------------------|
| 001:FR-029 (generate an override; never modify the compose file) | **Touched** by Story 1: wtenv's isolation settings live somewhere other than the auto-loaded override name when the repository owns that name (FR-003, FR-009). The compose file and the repository's override are never modified. |
| 001:FR-030 (plain Compose commands are isolated) | **Relaxed**, only for worktrees whose repository owns its override: there, Compose commands are isolated only when run through `wtenv exec`, a plain `docker compose` command runs without isolation, and `up` and the documentation say so (FR-004, FR-033). Unchanged for every other worktree (FR-008). |
| 001:FR-031, 001:FR-032, 001:FR-033 (ports tied to variables; block size; never un-remapped) | **Extended**: they apply to the combined compose file and repository override (FR-003). 001:FR-031's output also shows variable names (FR-014). Block sizing unchanged (FR-015). **Narrowed**: 001:FR-031 no longer accepts the three setups FR-028 names; they are refused with `port_variable_conflict` (FR-012). |
| 001:FR-015, FR-016, FR-017, FR-079, FR-080 (env file) | **Touched** by Stories 2 and 3: new variables inside wtenv's section (FR-010, FR-019). Section format, merge rules, duplicate warning, and byte-identical repeat unchanged. |
| 001:FR-020 to FR-027 (database isolation) | **Extended** by Story 3 with a third mode (FR-017 to FR-027). Host Postgres and SQLite modes unchanged. 001:FR-025 and FR-026 apply to the new mode. 001:FR-022 (database name differs between worktrees) is met through the project, port, and volume, not the database name. |
| 001:FR-034 (`up` does not start containers) | Unchanged. |
| 001:FR-036 (post-up commands run with the worktree's variables) | **Extended**: in a worktree whose repository owns its override, post-up commands also get the isolation settings, as `exec` does (FR-033). In every worktree, `up` runs none of them when an inherited `COMPOSE_FILE` names another worktree's isolation settings: it fails with `isolation_unavailable` first, as `exec` does (FR-033). |
| 001:FR-038, FR-039 (what `down` removes) | **Touched**: `down` removes wtenv's isolation settings wherever they live (FR-009) and never the repository's override (FR-002). The removal rules themselves are unchanged. |
| 001:FR-048 (`ls` columns) | **Extended**: variable names next to published ports (FR-014). |
| 001:FR-056 (`exec`) | **Extended**: the new variables are wtenv-managed, so `exec` sets them. In a worktree whose repository owns its override, `exec` is also how Compose commands pick up isolation (FR-004). |
| 001:FR-056, FR-057 (`exec` runs the command; its only refusal is "not provisioned") | **Relaxed**, only for worktrees whose repository owns its override: there `exec` refuses every command, not only Compose commands, with a stable error and without running it, when it cannot apply the isolation settings (FR-033). In every other worktree, `exec` refuses only when an inherited `COMPOSE_FILE` names another worktree's isolation settings, or, in the automatic arrangement, when an override file has appeared beside the compose file or the recorded one is a symbolic link or lost wtenv's header (FR-033). |
| 001:FR-063 (closed list of settings) | **Extended** by Story 3 (FR-017, FR-018). Story 1 and Story 2 add no setting. |
| 001:FR-087 (override header check) | **Guard unchanged**. One outcome added: a git-tracked file at the recorded path, header-less or still carrying wtenv's header, is adopted as the repository's and kept, instead of failing or being rewritten or deleted (FR-006). It is never written or deleted. |
| 001:FR-086, FR-088, FR-018, FR-039 volume rules, FR-070 | Unchanged, and applied to every new item (FR-009, FR-030). |
| All other spec 001 requirements | Unchanged. |

### Key Entities

- **Repository-owned override**: an override file the repository provides. wtenv reads it,
  never changes it, and records at most the fact that the worktree uses one.
- **Isolation settings**: the per-worktree project name and published-port mapping that wtenv
  generates and records. One per provisioned worktree with compose isolation. In a worktree
  whose repository owns its override, Compose picks them up only through `wtenv exec` and
  post-up commands.
- **Published port**: a service, a container port, and a protocol, mapped to one host port of
  the worktree's block. Has exactly one port variable.
- **Port variable**: a name, derived by the documented rule, and the host port it carries.
  Lives in wtenv's env-file section and in `exec`.
- **Compose-hosted database**: a named service of the worktree's compose project, its database
  port's published port, and the URL built from it. Its data belongs to the project's storage
  and is released with the project.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (acceptance)**: On one machine, three worktrees of
  `fastapi/full-stack-fastapi-template`, with no edit to any tracked file, run the template's
  dev flow for `backend`, `db`, `adminer`, and `mailpit` (Traefik and Playwright not started)
  at the same time, using the repository's own override file. All 15 published host ports
  (5 per worktree) are distinct and each lies in its worktree's block; the three `db` services
  use three distinct data volumes; and the repository's override file has the same content hash
  as the committed version before `up`, while the stacks run, and after `down`, in all three
  worktrees.
- **SC-002**: In each of those three worktrees, an agent finds the backend's host port by
  reading one variable whose name is the same in all three, and a request to that port reaches
  the backend of that same worktree, in 3 out of 3 worktrees.
- **SC-003**: In each of those three worktrees, a host-side command run through wtenv writes a
  distinct row to its database. Each row is found in its own worktree's database and in none of
  the other two (3 out of 3).
- **SC-004**: After `wtenv down` in all three worktrees, zero containers, networks, or
  project-scoped volumes of their three projects remain; the repository's override file and
  every resource outside those projects (including the main checkout's database volume, when
  one exists) are unchanged.
- **SC-005**: Running `up` again in a provisioned worktree yields identical ports, an identical
  project name, and a byte-identical env file and generated isolation settings, in 10 out of 10
  repeats, with and without a repository-owned override.
- **SC-006**: Every acceptance scenario of spec 001 still passes, and for repositories without
  an override file of their own the generated override is byte-identical to v0.1.1's, except
  for the three setups FR-028 names, which `up` refuses with `port_variable_conflict`.
- **SC-007**: Across all cleanup tests, including ones that plant repository-owned overrides,
  fixed-name volumes, external volumes, and files with wtenv-style names, wtenv writes, removes,
  or rewrites zero items that it did not create and record.
- **SC-008**: Across every compose setup in the test suite, no two published ports are given
  the same variable name: either the names differ, or `up` refuses before changing anything.
- **SC-009**: Every scenario above completes with no prompt and with output an agent can parse
  (`--json`), so that an agent can provision, find its ports and database, and tear down a
  worktree of the fastapi template with no human involved.

## Assumptions

- **Automatic, no new setting for Story 1**: a repository-owned override is detected and
  honoured whenever compose isolation is on. No setting turns it on or off.
- **Story 2 is on whenever compose isolation is on**: no setting is added for it (Principle V).
  Existing compose users see new lines in wtenv's section after upgrading (FR-028).
- **The env file stays where it is**: the default env file remains `.env.local`, and wtenv
  never writes a tracked file, including a tracked `.env` (001:FR-018). Host-side tools in a
  repository whose app reads a tracked `.env` reach the worktree's values through `wtenv exec`,
  whose variables take precedence over values inherited from the calling environment. Whether
  the app prefers environment variables over its own `.env` is the app's behaviour; the fastapi
  template does.
- **Credentials for a compose-hosted database** come from the developer's environment, as in
  v0.1.x (`{env:NAME}`), and may also come from the values Compose itself uses for interpolation
  in that worktree (for example a password in the repository's `.env`), so that the fastapi
  template needs no extra export. Planning confirms how; FR-020 applies either way.
- **The database URL variable is still `DATABASE_URL`** with no rename setting (that item stays
  on `docs/roadmap.md`). An app that reads separate host and port settings uses the Story 2
  port variable.
- **A compose-hosted database starts empty** (or as its image and the app's own start-up steps
  leave it). Copying it from a template, a snapshot, or another worktree is out of scope.
- **Isolation of a compose-hosted database comes from the compose project**: its own container,
  network, published port, and project-scoped volume (spec 001 assumption "Shared Postgres
  server"). The database name inside the server may be the same in every worktree.
- **Fixed `image:` names**: the fastapi template builds `backend` under a fixed image name, so
  the three worktrees share that image tag. SC-001 builds the same code in all three, so this
  does not affect the acceptance run; the warning for it stays on the roadmap.
- **Block size for the acceptance run**: the fastapi template publishes 8 host ports across all
  services, plus `PORT`, which fits the default block of 10.
- **Downgrade**: once a newer wtenv has recorded a worktree in a new mode, running v0.1.x
  against the same registry is not supported; v0.1.x stops with `registry_unreadable` and
  changes nothing (001:FR-070).
- **`exec` checks every command it runs** in a worktree whose repository owns its override, not
  only Compose commands, because it cannot tell which commands start Compose (FR-033). A
  developer who needs to run a command without those checks runs it without `wtenv exec`.
- **`COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` in the environment `exec` is called from, or in
  `.env` beside the compose file, or `COMPOSE_ENV_FILES` in that environment,** makes `exec` fail rather than be replaced silently, matching what `up` does for the same variables
  (FR-033).
- **A Compose command's own `-p` or `-f` arguments win** over the isolation settings that `exec`
  applies; such a command is not isolated. This follows from "several compose files and `-f`
  arguments" being out of scope.
- **Where the database keeps its data** (FR-027) is judged on the compose setup as Compose
  resolves it, with variables interpolated and under the worktree's own project name, so a
  volume's name is compared with the name it gets when the stack runs under that project. A
  volume has a fixed name by the rule of 001 reading R8. A host directory is inside or outside the worktree by its real path, after following
  symbolic links. FR-027 applies only to the database service; other services' fixed-name
  volumes keep the v0.1.x warning (reading R8).
- **Error and warning code names** for FR-027, FR-033, and the other new conditions are chosen
  in planning, under FR-031.
- **Compose version**: the minimum supported Compose version stays 2.24.4 unless planning finds
  that the Story 1 arrangement needs a later one, in which case the spec is updated first.
- **Platforms**: macOS and Linux, Windows through WSL2 only, as in spec 001.

### Known limits

- **`.env` files that wtenv does not check.** Compose reads the `.env` of the directory a
  command runs from (Compose 2.24.4: instead of the project directory's; Compose 5.1.4: as well
  as it, and its values win), and any file named by `COMPOSE_ENV_FILES` or `--env-file`. Each can
  set `COMPOSE_PROJECT_NAME` and so move the stack to another project (research R7). Through
  `wtenv exec` and post-up commands in a worktree whose repository owns its override, Compose
  reads only the `.env` beside the compose file, which `exec` checks, and an inherited
  `COMPOSE_ENV_FILES` makes `exec` fail (FR-033). Everywhere else this is **not detected**: a
  plain `docker compose` in any worktree, and `wtenv exec` or post-up commands in the automatic
  arrangement, behave as in v0.1.1. A command's own `--env-file` wins, as `-p` and `-f` do.
- **`.env` line forms.** wtenv recognises `KEY=value` and `KEY: value`, with or without `export`
  and leading spaces, after an optional byte-order mark (research R7). A line it reads as
  setting `COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` to a non-empty text makes `up` and `exec`
  fail, even when Compose would expand that text to an empty value.

### Out of Scope

Already on `docs/roadmap.md`, and not delivered by this feature:

- `wtenv up --dry-run`.
- `doctor` validating `wtenv.toml`, existing override files, and the Compose version.
- `doctor` showing the Docker engine and Compose plugin versions.
- A warning for a fixed `image:` name that worktrees share.
- `down` warning that a Compose client (such as `docker compose watch`) is still running.
- The exit-code-only checks.

Also not in this feature:

- The items listed under "Chosen for feature 003 (2026-10-05)" in `docs/roadmap.md`: the agent
  brief (`wtenv context`), database checkpoints, and `up --db-from`. They stay on the roadmap.
- Compose files with other names, several compose files, or `-f` arguments.
- Seeding a compose-hosted database from a template, a snapshot, or another worktree.
- Compose-hosted database engines other than Postgres (spec 001, Out of Scope).
- Running several Traefik proxies side by side, or routing by host name between worktrees.
- Removing anonymous volumes or fixed-name volumes (both stay on the roadmap).
- A setting to rename `DATABASE_URL`.
