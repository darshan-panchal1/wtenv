# Contract delta: `wtenv.toml` (spec 002)

**Feature**: `002-compose-dev-setup` | **Contract version**: 1 (additions only) | **Date**: 2026-10-10

This file lists only what spec 002 changes in
[001 contracts/config.md](../../001-worktree-runtime-isolation/contracts/config.md). Every
setting, rule and error not mentioned here is unchanged. Every `wtenv.toml` that v0.1.1 accepts
is accepted unchanged and produces the same ports, project name, database and generated
override (FR-028).

Stories 1 and 2 add **no** setting. A repository-owned override is detected and honoured
whenever `[compose]` is configured. The port variables are written whenever `[compose]` is
configured (spec, Assumptions; Principle V).

## Example: the fastapi template

```toml
ports = ["PORT"]

[compose]
file = "compose.yml"

[database]
type = "compose"
service = "db"
url = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/app"
```

## Settings: changed rows

| Setting | Type | Default | Rules |
|---------|------|---------|-------|
| `database.type` | `"postgres"`, `"sqlite"` or **`"compose"`** | required | `"compose"`: the database is a service of the worktree's own Compose project (FR-017). Requires `[compose]`; otherwise `config_invalid`, `setting` `database.type`. |
| `database.template` | string | required for `postgres` and `sqlite` | **Not allowed** with `type = "compose"`: `config_invalid`, `setting` `database.template` ("not a setting of type compose"). A worktree has one database mode (FR-017). |
| `database.service` *(new)* | string | required for `compose` | The name of the compose service that runs the database. **Not allowed** with `postgres` or `sqlite`: `config_invalid`, `setting` `database.service`. At `up`, the service must exist in the combined model of the compose file and the repository's override, and publish exactly one port (FR-021). Otherwise `config_invalid`, `setting` `database.service`, naming the service and what was found, and nothing changes. |
| `database.url` | string | required | Pattern for `DATABASE_URL`. For `compose`, see below. |

The database URL is still written to `DATABASE_URL`. There is still no setting to rename it
(spec, Assumptions).

## The URL pattern for `type = "compose"`

| Placeholder | Replaced by |
|-------------|-------------|
| `{port}` *(new)* | The host port wtenv assigned to the database service's one published port (FR-018). The same number is in the service's port variable, for example `WTENV_PORT_DB_5432_TCP` (FR-019). |
| `{env:NAME}` | Unchanged: the environment variable `NAME` when `up` runs, inserted as it stands. |
| `{compose_env:NAME}` *(new)* | The value of `NAME` in the database service's `environment` **as Compose resolved it**, including values from its `env_file` and from interpolation of the repository's `.env` (research R3). The value is **percent-encoded** (`urllib.parse.quote(value, safe="")`) because it is a raw value such as a password. A name the service's environment does not have: `config_invalid`, `setting` `database.url`, `details.variable`. |

`{name}` and `{path}` are not placeholders of this type: `config_invalid`.

**Compose patterns** must:

- use the scheme `postgresql://` or `postgres://`, with or without a driver suffix
  (spec 002 covers Postgres only; spec, Out of Scope);
- name the host explicitly as `localhost`, `127.0.0.1`, or `[::1]` (001:FR-025, applied by
  FR-018). Any other host, a missing host, or a `host`, `hostaddr` or `service` query
  parameter is `config_invalid`;
- have `{port}` as the port, and nothing else there;
- have a database name, which is used as written (for example `/app`). The database name may be
  the same in every worktree: isolation comes from the project (spec, Assumptions).

The message of any pattern error never repeats the pattern, which can hold a password
(001:FR-019).

**Credentials** (FR-020): `{env:NAME}` and `{compose_env:NAME}` keep passwords out of
`wtenv.toml`. Their values go into `DATABASE_URL` in wtenv's env-file section and into `exec`,
and nowhere else: not into the registry, not into any output.

wtenv never connects to a compose-hosted database (FR-022), so the pattern's user and password
are not checked by `up`.

## Changes after provisioning: new rows

| Change | Effect |
|--------|--------|
| `database.type` changed to `"compose"` from `postgres` or `sqlite` | `DATABASE_URL` is rebuilt from the new pattern. The old database stays recorded until `down` (001:FR-065, unchanged) |
| `database.type` changed from `"compose"` to `postgres` or `sqlite` | The new kind is created as in v0.1.x. Nothing was recorded for the compose database, and its data stays in the project's volume until `down` |
| `database.service` | The URL and its port follow the new service; nothing is removed |
| A repository-owned override appears or disappears (not a setting) | The next `up` switches the compose arrangement ([data-model.md](../data-model.md#transitions-step-8-records-them-step-11-carries-them-out)) |
