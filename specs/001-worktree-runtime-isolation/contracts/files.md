# Contract: files and names wtenv writes

**Feature**: `001-worktree-runtime-isolation` | **Contract version**: 1 | **Date**: 2026-10-03

These formats are part of the stable interface: developers read these files, and other tools
load them. The registry file is described in [data-model.md](../data-model.md#registry-file).

## Names

`<id8>` is the first 8 hexadecimal digits of the SHA-256 of the worktree's git directory
path. `<slug>` is the worktree directory's name when the worktree was first provisioned,
lowercased, with every run of characters outside `a–z` and `0–9` replaced by one `_` (for
databases) or `-` (for compose), trimmed of those characters at both ends, cut to 40
characters, and trimmed again at the end. An empty slug becomes `wt`. Both are recorded at
creation and never recomputed, so they survive a move (FR-022, FR-084).

| What | Name | Example |
|------|------|---------|
| Postgres database | `wtenv_<slug>_<id8>` | `wtenv_feature_x_3f9a1c2b` |
| Compose project | `wtenv-<slug>-<id8>` | `wtenv-feature-x-3f9a1c2b` |
| SQLite copy | `<worktree>/.wtenv/<template file name>`; `.wtenv/` and the copy are never symbolic links (FR-086) | `/code/feature-x/.wtenv/dev.sqlite3` |
| Compose override | Beside the compose file: `compose.override.yaml` for `compose.yaml`, `compose.override.yml` for `compose.yml`, `docker-compose.override.yaml` for `docker-compose.yaml`, `docker-compose.override.yml` for `docker-compose.yml` | `/code/feature-x/compose.override.yaml` |

All names start with `wtenv`, so they are recognisable (FR-022). Recognising a name is never
a reason to remove something; only the registry is (FR-039).

## Env file section

```text
# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>
PORT=20010
DB_PORT=20011
DATABASE_URL=postgresql://myapp:s3cr%3Ft@localhost:5432/wtenv_feature_x_3f9a1c2b
# <<< wtenv managed <<<
```

- The two marker lines are exactly as shown (FR-016). Lines between them are owned by wtenv;
  everything else in the file belongs to the developer and is preserved byte for byte.
- Lines inside: the port variables in `ports` order, then `DATABASE_URL` when a database is
  configured and created. One `NAME=value` per line, ending in `\n`.
- Quoting: a value made only of `A–Z a–z 0–9 _ . / : @ % + = , ~ -` is written bare; any
  other value is written in single quotes. Values never contain a single quote or a line
  break (see [config.md](config.md#the-url-pattern)).
- First write: the section is appended at the end of the file. If the file does not end with
  a line break, wtenv adds one first and records that it did; `down` removes that line break
  again (FR-079).
- Later writes: the section is rewritten where it is, even if the developer moved it.
- `wtenv exec` reads its variables from this section: each `NAME=value` line, with the
  single quotes of a quoted value removed. Values are passed as they stand in the file, so a
  hand edit inside the section reaches the command until the next `up` rewrites it.
- Damaged markers (a begin with no end, an end with no begin, or two sections): `up` and
  `exec` fail with `env_file_unusable`, reason `markers_damaged`; `down` reports the section
  under `failed` (FR-081).
- A file wtenv creates gets mode `0600` (FR-019). An existing file keeps its mode. Writes go
  through a temporary file and an atomic rename.
- Symbolic links (FR-086): wtenv never writes or deletes through one. An env file that is a
  symbolic link, or that sits in a directory inside the worktree that is one, makes `up`
  fail with `env_file_unusable`, reason `symlink`; `down` and `gc` report the section under
  `failed` with reason `symlink` and leave the link and its target as they are.
- Marker lines are recognised with trailing whitespace or a trailing carriage return.

## `.git/info/exclude` block

```text
# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>
/.env.local
/.wtenv/
/compose.override.yaml
# <<< wtenv managed <<<
```

- The file is the repository's shared one, found with
  `git rev-parse --path-format=absolute --git-path info/exclude` (FR-018).
- Lines inside: the sorted union of the generated paths of every registered worktree of the
  repository, each anchored with a leading `/`, which git reads as relative to each
  worktree's root.
- `up` only adds lines. The block is removed, markers included, when `down` or `gc` releases
  the last registered worktree of the repository (FR-085).
- Every other line of the file is preserved. The block is read and written only while the
  registry lock is held, so two `up` runs in different worktrees cannot lose each other's
  lines.
- Damaged markers: `up` fails with `unsupported`, reason `markers_damaged`.

## Git hook block

Written by `wtenv hook install` into `<git-common-dir>/hooks/post-checkout`, directly after
the shebang line. A new file gets the shebang `#!/bin/sh` and mode `0755`.

```sh
# >>> wtenv managed (written by `wtenv hook install`; do not edit) >>>
if [ "$3" = "1" ] && [ -n "$1" ] && [ -z "$(printf '%s' "$1" | tr -d 0)" ]; then
  if [ "$(git rev-parse --git-dir 2>/dev/null)" != "$(git rev-parse --git-common-dir 2>/dev/null)" ]; then
    if command -v wtenv >/dev/null 2>&1; then
      wtenv up 1>&2 || true
    else
      echo "wtenv: not found on PATH; this worktree was not provisioned" 1>&2
    fi
  fi
fi
# <<< wtenv managed <<<
```

- Runs `wtenv up` only for a new linked worktree: a branch checkout (`$3` is `1`) from the
  all-zero object name (`$1`; 40 or 64 zeros), in a worktree whose git directory is not the
  common one. `git clone`, `git switch`, and file checkouts do nothing (FR-051).
- Never changes the hook's exit status and never stops the rest of the hook (FR-052).
- Existing hook content is never altered; `hook uninstall` removes exactly the block
  (FR-053).
- An existing hook counts as a POSIX shell script when its shebang names `sh`, `bash`,
  `dash`, or `ksh`. Any other shebang, or none, is `unsupported`, reason `hook_not_shell`.

## Compose override file

Generated by `up`; the exact layout below, so a repeat `up` produces identical bytes (US3
scenario 4).

```yaml
# Generated by wtenv for this worktree. Do not edit or commit; `wtenv up` rewrites it.
name: "wtenv-feature-x-3f9a1c2b"
services:
  "cache":
    ports: !override
      - {"target": 6379, "published": "20012", "protocol": "tcp", "mode": "ingress"}
  "db":
    ports: !override
      - {"target": 5432, "published": "20011", "protocol": "tcp", "mode": "ingress"}
```

- The file is never written or deleted through a symbolic link (FR-086): `up` fails with
  `env_file_unusable`, reason `symlink`, and `down` and `gc` report the override under
  `failed` with reason `symlink`.
- `name`: the worktree's compose project name.
- One entry per service that publishes ports, in service-name order. Services without
  published ports are left out.
- `ports: !override` replaces the service's whole list (Compose 2.24.4 or later). Each
  mapping repeats every field Compose reported for it (`target`, `protocol`, `mode`, and
  `host_ip`, `name`, `app_protocol` when present), in that order, with only `published`
  changed. Container ports are never changed (FR-029).
- Strings are written with JSON quoting, which YAML reads the same way; no YAML library is
  used.

## State directory

`<state>` is `platformdirs.user_state_path("wtenv", appauthor=False)`:
`~/Library/Application Support/wtenv` on macOS, `$XDG_STATE_HOME/wtenv` (default
`~/.local/state/wtenv`) on Linux and WSL2. `XDG_STATE_HOME` moves it on both.

| Path | Mode | Purpose |
|------|------|---------|
| `<state>/` | `0700` | Created on first use |
| `<state>/registry.json` | `0600` | The registry (FR-066); written by temporary file and atomic rename |
| `<state>/registry.lock` | `0600` | Registry lock (10 s bound) |
| `<state>/locks/<id16>.lock` | `0600` | One worktree lock per git directory (60 s bound); `<id16>` = first 16 hex digits of the SHA-256 of the git directory path. Never deleted. |
