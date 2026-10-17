# Changelog

All notable changes to wtenv are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and wtenv uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-17

Support for repositories that own their Compose override, port variables for every published
port, and a database that runs as a Compose service.

### Added

- **A repository-owned override.** When the repository tracks its own override beside the compose
  file, `wtenv up` no longer refuses it. It never writes, renames or deletes the repository's
  file. It writes `.wtenv/compose.isolation.yaml`, and `wtenv exec` and `post_up` commands tell
  Compose to load it, so every published port, including those only the override declares, is
  remapped into the worktree's block (the exec-only arrangement). `up` reports it in text and in
  `worktree.compose_isolation` and `worktree.compose_repository_override` (also in `ls --json`),
  and warns `compose_isolation_exec_only` on every `up`.
- **Port variables.** With `[compose]`, one `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>`
  variable per published port, in the env file and in `wtenv exec`.
- **`[database] type = "compose"`**, with `service`, the `{port}` placeholder and
  `{compose_env:NAME}` (percent-encoded): the database is a service of the worktree's own
  Compose project. `up` finds its one published port, writes `DATABASE_URL`, and refuses shared
  data storage (`database_storage_shared`).
- Error codes `isolation_unavailable` (20; `exec` reports it with exit status 125),
  `override_ambiguous` (21), `port_variable_conflict` (22) and `database_storage_shared` (23).
- Warnings `compose_isolation_exec_only`, `compose_database_data_kept`,
  `compose_override_adopted` and `compose_override_excluded`.
- `kept_items` in the JSON of `down` and `gc`: a git-tracked file at the recorded override path is
  kept, never deleted.

### Changed

- **Behaviour change.** Three compose setups that v0.1.1 accepted now fail with
  `port_variable_conflict` (exit 22): a setup that publishes one container port twice (for
  example a repository override that adds a second host port for the same container port); two
  service names that map to one variable name (`my-api` and `my_api`, `API` and `api`); and a
  `ports` entry named like a `WTENV_PORT_…` variable.
- Existing compose users see new `WTENV_PORT_…` lines in wtenv's env-file section, reported
  `updated` on the first `up`.
- `up` refuses `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` in the `.env` forms `KEY: value` and
  after a byte-order mark, as it already did for `KEY=value`.
- `exec` exits 125 when an inherited `COMPOSE_FILE` names another worktree's isolation file, or
  when an override file appeared beside wtenv's own override since `up`.
- A git-tracked generated override is adopted and kept, never rewritten or deleted.

### Known limits

- A plain `docker compose` in a worktree whose repository owns its override is not isolated; use
  `wtenv exec -- docker compose ...`.
- Before downgrading to v0.1.x, run `wtenv down` in every worktree whose repository owns its
  override, or the registry becomes unreadable to v0.1.x for every worktree.
- `gc --release` cannot see that a file is tracked; a tracked file that still carries wtenv's
  header is removed, as in v0.1.1. `down` and `gc` text output does not list `kept_items`; the
  JSON does.

See the README for the full list.

## [0.1.1] - 2026-10-09

Fixes from a run of 0.1.0 on `fastapi/full-stack-fastapi-template`.

### Fixed

- `wtenv ls` now shows the remapped ports that compose services publish, in a `PUBLISHED`
  column (`backend:8000->20001`), as `wtenv up` already did (FR-031; T231, T232). The column
  appears only when some worktree publishes a port. `ls --json` already carried them.
- `wtenv doctor` no longer creates the state directory or `registry.lock` when there is no
  registry yet; it changes nothing, as `--help` says (FR-060; T233, T234).
- `wtenv ls`, `wtenv gc --dry-run` and `wtenv down --dry-run` no longer create the state
  directory or `registry.lock` on a fresh `XDG_STATE_HOME`, in text or `--json` (FR-040,
  FR-050; T237, T238).

### Documentation

- README: the override file is named after the compose file (`.yaml` or `.yml`); the registry
  location follows `XDG_STATE_HOME`; how to merge an override of your own into the compose file
  without pasting `docker compose config` output, which adds fixed `name:` lines that make
  worktrees share volumes; Compose reads `.env`, not `.env.local`; stop `docker compose watch`
  and any foreground `up` before `wtenv down` (T235).

## [0.1.0] - 2026-10-08

First release. Nine commands, one registry, no daemon.

### Added

- `wtenv up`: gives a git worktree its own block of ports and its own env file section, and
  repeats safely. With `wtenv.toml` it also creates a Postgres database (a copy of a template
  database) or a SQLite copy per worktree, a docker-compose override with its own project name
  and remapped host ports, and runs `post_up` commands.
- `wtenv down`: releases everything recorded for the current worktree, with `--dry-run`.
- `wtenv gc`: releases the entries of worktrees that git confirms are gone, with `--dry-run`
  and `--release PATH` for entries that cannot be confirmed.
- `wtenv ls`, `wtenv exec`, `wtenv doctor`.
- `wtenv hook install` and `wtenv hook uninstall`: a marked `post-checkout` block that runs
  `wtenv up` for `git worktree add`.
- `--json` on every command: exactly one JSON document on standard output, with stable error
  codes and exit statuses.
- Recovery after an interrupted `up` or `down`: every resource is recorded before it is
  created or removed.
- Safety rules for removal: only what the registry records is removed, recorded values are
  checked before they are used, and symbolic links are never followed.

### Known limits

- Anonymous compose volumes, and volumes with a fixed `name:`, stay on disk and are listed
  under `kept_volumes`.
- Postgres over local TCP only. macOS and Linux (including WSL2); no native Windows.
- Compose files must use one of the four default file names; a host port range onto one
  container port is not supported.

See the README for the full list.

