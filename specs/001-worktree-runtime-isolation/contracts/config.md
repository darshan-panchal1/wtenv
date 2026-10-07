# Contract: `wtenv.toml`

**Feature**: `001-worktree-runtime-isolation` | **Contract version**: 1 | **Date**: 2026-10-03

`wtenv.toml` is optional. It lives in the root of a worktree, is committed with the
repository, and is read from the worktree a command runs in (FR-062). It holds exactly the
settings below (FR-063). Any other key, or an invalid value, is a `config_invalid` error that
names the setting, and the command changes nothing (FR-064).

Without the file, `up` behaves as if it contained only the defaults: port and env-file
isolation, no database, no compose (FR-005, Principle V).

## Example

```toml
# Port variables, in order. Each gets its own port from the worktree's block.
ports = ["PORT", "DB_PORT", "VITE_PORT"]
block_size = 10
env_file = ".env.local"
post_up = ["uv run alembic upgrade head"]

[database]
type = "postgres"
template = "myapp_template"
url = "postgresql://myapp:{env:MYAPP_DB_PASSWORD}@localhost:5432/{name}"

[compose]
file = "compose.yaml"
```

## Settings

| Setting | Type | Default | Rules |
|---------|------|---------|-------|
| `ports` | list of strings | `["PORT"]` | At least one name. Names are unique and match `[A-Za-z_][A-Za-z0-9_]*`. `DATABASE_URL` is not allowed. Each gets a different port of the block, in this order (FR-014). |
| `block_size` | integer | `10` | 1 to 1000. Must be at least the number of `ports` plus the compose ports not tied to a variable; otherwise the error states the smallest size that fits (FR-032). Changing it moves the worktree to a new block (FR-065). |
| `env_file` | string | `".env.local"` | A relative path inside the worktree; `..` may not leave the worktree. Its directory must exist. The file may not be tracked by git (FR-018). |
| `post_up` | list of strings | `[]` | Shell commands, run in order with `sh -c` after every successful provisioning (FR-036). Each must be non-empty. |
| `database` | table | absent | Present: database isolation is on (FR-020, FR-021). |
| `database.type` | `"postgres"` or `"sqlite"` | required | |
| `database.template` | string | required | Postgres: the name of the template database on the shared server. SQLite: the path of the template file, relative to the worktree root or absolute. |
| `database.url` | string | required | Pattern for `DATABASE_URL`. See below. |
| `compose` | table | absent | Present: compose isolation is on (FR-028). |
| `compose.file` | string | required | Relative path of the compose file inside the worktree. Its file name must be `compose.yaml`, `compose.yml`, `docker-compose.yaml`, or `docker-compose.yml`, because only those are found by plain `docker compose` (FR-030; research.md, section 4). |

The database URL is always written to the variable `DATABASE_URL`. The spec's FR-063 list
has no setting to rename it, so v1 does not offer one.

## The URL pattern

| Placeholder | Replaced by |
|-------------|-------------|
| `{name}` | Postgres: the worktree's database name, for example `wtenv_feature_x_3f9a1c2b` |
| `{path}` | SQLite: the absolute path of the worktree's copy of the template |
| `{env:NAME}` | The value of the environment variable `NAME` when the command runs. Use this for passwords, so they are not committed (FR-026). An unset variable is `config_invalid` with `details.variable`. |

Any other text in braces is `config_invalid`.

**Postgres patterns** must:

- use the scheme `postgresql://` or `postgres://`, with or without a driver suffix such as
  `postgresql+psycopg://` (the suffix is kept in `DATABASE_URL` and dropped for wtenv's own
  connection);
- contain `{name}` as the database name;
- name the host explicitly as `localhost`, `127.0.0.1`, or `[::1]`. Any other host, a missing
  host, a `host`, `hostaddr`, or `service` query parameter, or a Unix-socket directory is
  `config_invalid` (FR-025).

wtenv's own connection uses the pattern's user, password, host, and port, and the database
`postgres`. Query parameters are kept in `DATABASE_URL` and not used by wtenv.

**SQLite patterns** must contain `{path}`, for example `sqlite:///{path}`.

**Values written to the env file** follow the quoting rule in
[files.md](files.md#env-file-section). A resolved URL containing a single quote or a line
break is `config_invalid`; percent-encode such characters.

## Changes after provisioning

The next `up` applies the change (FR-065):

| Change | Effect |
|--------|--------|
| `ports` list | Ports reassigned within the same block, in the new order |
| `block_size` | New block allocated, old block released, change reported; database untouched |
| `env_file` | Section written to the new file; the section in the old file is removed |
| `database.template` or `database.url` | `DATABASE_URL` rewritten; the existing database is kept, never re-created from the new template |
| `database.type` | The new kind is created; the old database stays recorded until `down` |
| `[database]` removed | `DATABASE_URL` removed from the section; the database stays recorded until `down` |
| `compose.file` | Override moves to the new location; the project name stays the same |
| `[compose]` removed | wtenv's override file is removed; the project stays recorded until `down` |
| `post_up` | The new list runs |
