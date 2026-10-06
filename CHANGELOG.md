# Changelog

All notable changes to wtenv are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and wtenv uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - Unreleased

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

