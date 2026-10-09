# Changelog

All notable changes to wtenv are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and wtenv uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

