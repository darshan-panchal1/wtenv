# wtenv

Give each git worktree its own runtime: a port block, a database, a docker-compose project,
and an env file. Remove it all again when the worktree goes.

Two coding agents in two worktrees both start a dev server on port 3000, both migrate the
same database, and both `docker compose up` the same project, so one overwrites the other.
`wtenv up` gives each worktree its own ports, its own copy of your database, its own compose
project, and its own `.env.local`. `wtenv down` and `wtenv gc` take them away, and only what
wtenv recorded creating.

wtenv is a local command-line tool for developers who run two to five coding agents (Claude
Code, Codex, Cursor) in parallel worktrees. It starts no server and runs no daemon. It makes
no network call except to the Postgres server and the Docker engine on your own machine.

## 60-second demo: three agents, three worktrees

`wtenv.toml` in the repository (this file is optional; without it you get ports and an env
file only):

```toml
ports = ["PORT", "DB_PORT"]
```

One worktree per agent, each provisioned by running `wtenv up` in it:

```text
$ git worktree add ../myapp-agent-1 -b agent-1
$ git worktree add ../myapp-agent-2 -b agent-2
$ git worktree add ../myapp-agent-3 -b agent-3

$ cd ../myapp-agent-1 && wtenv up
wtenv: provisioned /code/myapp-agent-1
  ports      20000-20009  PORT=20000 DB_PORT=20001
  env file   .env.local (created)
$ cd ../myapp-agent-2 && wtenv up
wtenv: provisioned /code/myapp-agent-2
  ports      20010-20019  PORT=20010 DB_PORT=20011
  env file   .env.local (created)
$ cd ../myapp-agent-3 && wtenv up
wtenv: provisioned /code/myapp-agent-3
  ports      20020-20029  PORT=20020 DB_PORT=20021
  env file   .env.local (created)
```

Each worktree now has its own block of ten ports and a `.env.local` that git does not show:

```text
$ cat .env.local
# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>
PORT=20010
DB_PORT=20011
# <<< wtenv managed <<<

$ wtenv ls
STATUS         PORTS        VARIABLES                 DATABASE  COMPOSE  PATH
provisioned    20000-20009  PORT=20000 DB_PORT=20001  -         -        /code/myapp-agent-1
provisioned    20010-20019  PORT=20010 DB_PORT=20011  -         -        /code/myapp-agent-2
provisioned    20020-20029  PORT=20020 DB_PORT=20021  -         -        /code/myapp-agent-3
unprovisioned  -            -                         -         -        /code/myapp
```

Start each agent's server through `wtenv exec`, which adds the variables of the worktree's
`.env.local` section to a command's environment and then runs it:

```text
$ cd ../myapp-agent-1 && wtenv exec -- npm run dev
```

When an agent is finished, release its resources and remove the worktree. Or remove the
worktree first and let `gc` clean up after it:

```text
$ cd ../myapp-agent-3 && wtenv down
wtenv: released for /code/myapp-agent-3
  removed      env_section /code/myapp-agent-3/.env.local
  removed      env_file /code/myapp-agent-3/.env.local
  removed      port_block 20020-20029
  removed      registry_entry /code/myapp/.git/worktrees/myapp-agent-3

$ git worktree remove --force ../myapp-agent-2
$ wtenv gc
wtenv: released 1 worktree(s)
  /code/myapp-agent-2
    removed      port_block 20010-20019
    removed      registry_entry /code/myapp/.git/worktrees/myapp-agent-2
    already gone env_section /code/myapp-agent-2/.env.local
```

Add a database and a compose project in `wtenv.toml` and the same two commands also create
and remove a Postgres database per worktree and a compose project per worktree. See
[`wtenv.toml`](#wtenvtoml).

## Install

```sh
uv tool install wtenv
# or
pipx install wtenv
```

Requirements:

| Need | When |
|------|------|
| Python 3.11 or later | Always (`uv tool install` fetches one if needed) |
| git 2.31 or later | Always |
| A local PostgreSQL server, 13 or later | Only with `[database] type = "postgres"` |
| Docker with Compose 2.24.4 or later | Only with `[compose]` |

Platforms: macOS, Linux, and Windows through WSL2. Not native Windows.

## Commands

wtenv has nine commands and no others. `up`, `down`, `exec`, `hook install`, and
`hook uninstall` act on the worktree that contains the current directory. `ls`, `gc`, and
`doctor` run anywhere. wtenv never prompts and never reads standard input (except `exec`,
which passes it to the command it runs).

| Command | What it does | Example |
|---------|--------------|---------|
| `wtenv --version` | Prints the version | `wtenv --version` |
| `wtenv up` | Provisions the current worktree, or brings it up to date with its `wtenv.toml`. Running it again with nothing changed changes nothing | `wtenv up` |
| `wtenv down [--dry-run]` | Releases everything recorded for the current worktree: compose project, database, port block, env-file section | `wtenv down --dry-run` |
| `wtenv gc [--dry-run] [--release PATH]...` | Releases the entries of worktrees that git confirms are gone, across all repositories. `--release PATH` names an entry whose worktree cannot be confirmed gone, such as a deleted repository | `wtenv gc` |
| `wtenv ls` | Lists every known worktree with its status, ports, database, and compose project | `wtenv ls` |
| `wtenv exec -- COMMAND [ARG]...` | Runs `COMMAND` with the worktree's variables in its environment, and exits with its status | `wtenv exec -- pytest` |
| `wtenv doctor` | Checks git, Docker, Postgres, and the registry, and finds assigned ports that another process holds | `wtenv doctor` |
| `wtenv hook install` | Adds a `post-checkout` hook block, so `git worktree add` runs `wtenv up` in the new worktree | `wtenv hook install` |
| `wtenv hook uninstall [--dry-run]` | Removes exactly what `install` added | `wtenv hook uninstall` |

Every command takes `--json`.

wtenv keeps one registry per user, not one per repository: `registry.json` in the state
directory, which is `~/Library/Application Support/wtenv` on macOS and
`$XDG_STATE_HOME/wtenv` (default `~/.local/state/wtenv`) on Linux and WSL2. Setting
`XDG_STATE_HOME` moves it on both. Two shells with different values use two registries that know
nothing of each other's port blocks, so set it the same way everywhere you run wtenv, including
in the agents' environments.

### `--json` and exit statuses

With `--json`, standard output holds exactly one JSON document, and everything else goes to
standard error:

```text
$ wtenv --version --json
{"schema_version":1,"command":"version","ok":true,"error":null,"warnings":[],"version":"0.1.1"}

$ cd /tmp && wtenv up --json
{"schema_version":1,"command":"up","ok":false,"error":{"code":"not_in_worktree","exit_status":4,"message":"not inside a git worktree: /tmp","hint":"Run wtenv from a directory inside a git worktree.","details":{"cwd":"/tmp"}},"warnings":[],"worktree":null,"changes":[],"post_up":[]}
```

The error codes and their exit statuses are stable. Each failure has one code and one status:

| Exit | Code | Meaning |
|------|------|---------|
| 0 | | Success |
| 1 | `internal_error` | Unexpected failure inside wtenv |
| 2 | `usage_error` | Unknown command, option, or argument |
| 3 | `config_invalid` | `wtenv.toml` is invalid, or asks for more than the block holds |
| 4 | `not_in_worktree` | The current directory is not inside a git worktree |
| 5 | `not_provisioned` | The worktree has no completed `up` |
| 6 | `no_free_block` | No free port block in the range |
| 7 | `env_file_unusable` | The env file cannot be written safely |
| 8 | `dependency_unavailable` | git, Docker, or the Postgres server is missing, too old, not local, or refuses the connection |
| 9 | `template_missing` | The template database or file does not exist |
| 10 | `template_in_use` | The template database has open connections and cannot be copied |
| 11 | `ownership_conflict` | Something exists where wtenv would create a resource, and wtenv has no record of creating it |
| 12 | `post_up_failed` | A `post_up` command exited non-zero |
| 13 | `partial_failure` | `down` or `gc` could not remove some items; they stay recorded |
| 14 | `registry_busy` | The registry lock was not free within 10 seconds |
| 15 | `worktree_busy` | Another `up` or `down` held this worktree for more than 60 seconds |
| 16 | `registry_unreadable` | The registry cannot be read or locked; nothing was changed |
| 17 | `problems_found` | `doctor` found at least one problem |
| 18 | `worktree_exists` | `gc --release` named a worktree that still exists |
| 19 | `unsupported` | The request is valid, but v1 cannot carry it out safely in this setup |
| 20 | `isolation_unavailable` | `wtenv exec` cannot apply the worktree's isolation settings, so the command was not run. `exec` reports it with exit status 125, like every failure of wtenv itself. `up` raises it too, with 20 as its exit status, when `COMPOSE_FILE` names another worktree's isolation file and a `post_up` command would run. See `details.reason` and [the table below](#isolation_unavailable) |
| 21 | `override_ambiguous` | Two or more override files that your repository owns sit beside the compose file; Compose would load only one of them |
| 22 | `port_variable_conflict` | Two published ports would get the same port variable name, or a name equals a variable the configuration defines |
| 23 | `database_storage_shared` | The compose-hosted database keeps its data where other worktrees or the main checkout would share it |

`exec` is the one exception, and follows `env(1)`: it exits with the status of the command it
ran. It exits 125 when wtenv itself fails (for example `not_provisioned` or `isolation_unavailable`), 126 when the command
was found but could not be run, and 127 when it was not found.

## `wtenv.toml`

`wtenv.toml` is optional. It lives in the root of the worktree, is committed with the
repository, and is read from the worktree a command runs in. Any other key, or an invalid
value, is a `config_invalid` error that names the setting, and the command changes nothing.

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

| Setting | Type | Default | Rules |
|---------|------|---------|-------|
| `ports` | list of strings | `["PORT"]` | At least one name. Names are unique and match `[A-Za-z_][A-Za-z0-9_]*`. `DATABASE_URL` is not allowed. Each gets a different port of the block, in this order |
| `block_size` | integer | `10` | 1 to 1000. Must be at least the number of `ports` plus the compose ports not tied to a variable; otherwise the error states the smallest size that fits. Changing it moves the worktree to a new block |
| `env_file` | string | `".env.local"` | A relative path inside the worktree. Its directory must exist. The file may not be tracked by git |
| `post_up` | list of strings | `[]` | Shell commands, run in order with `sh -c` after every successful provisioning. Each must be non-empty |
| `database` | table | absent | Present: database isolation is on |
| `database.type` | `"postgres"`, `"sqlite"` or `"compose"` | required | `"compose"`: the database is a service of the worktree's own Compose project, see [A database that runs as a Compose service](#a-database-that-runs-as-a-compose-service). It requires `[compose]` |
| `database.template` | string | required for `postgres` and `sqlite` | Postgres: the name of the template database on the shared server. SQLite: the path of the template file, relative to the worktree root or absolute. Not allowed with `type = "compose"` |
| `database.service` | string | required for `compose` | The name of the Compose service that runs the database. Not allowed with the other types |
| `database.url` | string | required | Pattern for `DATABASE_URL`; see below |
| `compose` | table | absent | Present: compose isolation is on |
| `compose.file` | string | required | Relative path of the compose file inside the worktree. Its file name must be `compose.yaml`, `compose.yml`, `docker-compose.yaml`, or `docker-compose.yml` |

Ports come from the range 20000 to 29999, in blocks of `block_size`. Each worktree keeps its
block until `down` or `gc` releases it.

### The database URL pattern

The URL is written to `DATABASE_URL` in the env file. wtenv has no setting to rename the
variable.

| Placeholder | Replaced by |
|-------------|-------------|
| `{name}` | Postgres: the worktree's database name, for example `wtenv_feature_x_3f9a1c2b` |
| `{path}` | SQLite: the absolute path of the worktree's copy of the template |
| `{env:NAME}` | The value of the environment variable `NAME` when the command runs. Use it for passwords, so they are not committed. An unset variable is `config_invalid` |
| `{port}` | `type = "compose"` only: the host port wtenv assigned to the database service's one published port |
| `{compose_env:NAME}` | `type = "compose"` only: the value of `NAME` in the database service's `environment` as Compose resolved it, percent-encoded (`urllib.parse.quote(value, safe="")`), so a password holding `@`, `/`, `:` or `%` cannot change the host or the port. A name the service does not have is `config_invalid` |

Postgres: each worktree gets `CREATE DATABASE wtenv_<slug>_<id8> TEMPLATE <template>` on the
server named in the URL (`<slug>` comes from the worktree's directory name, `<id8>` is eight
hex digits that keep two worktrees with the same name apart), so the template must exist and have no open connections. The URL
must use `postgresql://` or `postgres://`, contain `{name}`, and name the host explicitly as
`localhost`, `127.0.0.1`, or `[::1]`. SQLite: the template file is copied to
`<worktree>/.wtenv/`, and the URL must contain `{path}`, for example `sqlite:///{path}`.

Passwords are never printed in any mode and never recorded in the registry.

### Compose

With `[compose]` and no override file of your own, `up` writes an override file beside your
compose file, named after it:
`compose.override.yaml` for `compose.yaml`, `compose.override.yml` for `compose.yml`, and
`docker-compose.override.yaml` or `.yml` for the `docker-compose.*` names. It sets the
project name to a name unique to the worktree and remaps every published host port to a port
of the worktree's block. Plain `docker compose up` in the worktree then uses them, with no
extra flags. Tie a service's port to one of your variables inside the compose file, the way
Compose users already do:

```yaml
services:
  cache:
    image: redis:7-alpine
    ports:
      - "${CACHE_PORT:-6379}:6379"
```

with `ports = ["PORT", "CACHE_PORT"]` in `wtenv.toml`. A published port that is not tied to a
variable gets the next free port of the block. The override file is added to
`.git/info/exclude`, as is the env file, so `git status` stays clean.

**If your repository already has an override file**, wtenv uses it; see
[A repository that owns its override](#a-repository-that-owns-its-override). `up` does not edit
your file, does not delete it, and no longer refuses it. Do not build a merged compose file from
the output of `docker compose config`: it adds a `name:` for the project, one for the default
network, and one for every volume. A fixed `name:` is the same in every worktree, so the main
checkout and every worktree would share those volumes (`up` warns with
`compose_fixed_volume_name`, and `down` keeps them). Use `docker compose config` to look at the
result, not to copy from it.

**Compose does not read `.env.local`.** Compose reads `.env` in the project directory, and
nothing else unless you pass `--env-file`. wtenv's default env file is `.env.local`, so
`docker compose` does not see its variables. The project name and the published ports do not
need them, because the override file sets both. If your compose file interpolates one of
wtenv's variables (for example `${DATABASE_URL}`), either set `env_file = ".env"` in
`wtenv.toml`, so that wtenv writes its marked section into `.env` and leaves your other lines
alone (the file must not be tracked by git), or run `docker compose --env-file .env.local ...`,
which then reads that file instead of `.env`.

**Stop Compose clients before `wtenv down`.** `down` removes the project's containers, network,
and labelled volumes, and then forgets the project. It does not track the Compose processes you
started. A `docker compose watch` that is still running re-creates the containers and the
network the next time a watched file changes, and by then nothing records the project, so
neither `down` nor `gc` will remove it. Stop `docker compose watch` and any foreground
`docker compose up` in the worktree first. If a project was re-created, find it with
`docker compose ls -a` and remove it with `docker compose -p NAME down`.

### A repository that owns its override

When your repository tracks its own override file (`compose.override.yml`, for example) beside
the compose file, `up` works with it. It never writes, renames or deletes your file and never adds
it to `.git/info/exclude`. Instead it writes `.wtenv/compose.isolation.yaml`, which sets the
project name and remaps every published port, including those only your override declares, into
the worktree's block. Compose loads that file only when `wtenv exec` (and the `post_up` commands)
tell it to, so you start the stack through `wtenv exec`:

(The worktree path is shortened to `/code/feature-x`; the rest is the output as printed.)

```text
$ wtenv up
wtenv: warning [compose_isolation_exec_only]: the repository owns compose.override.yml, so Compose commands are isolated only through `wtenv exec -- docker compose ...`; a plain `docker compose` here runs without isolation
wtenv: provisioned /code/feature-x
  ports      20000-20009  PORT=20000
  published  backend:6379 -> 20001 (WTENV_PORT_BACKEND_6379_TCP)  db:5432 -> 20002 (WTENV_PORT_DB_5432_TCP)
  database   compose service db on localhost:20002  (DATABASE_URL)
  compose    wtenv-feature-x-d3c81c41  .wtenv/compose.isolation.yaml (created)
  isolation  exec only: the repository owns compose.override.yml. Run Compose through
             `wtenv exec -- docker compose ...`; a plain `docker compose` here runs WITHOUT isolation.
  env file   .env.local (created)

$ wtenv exec -- docker compose up -d
$ docker ps --format "{{.Names}}  {{.Ports}}"
wtenv-feature-x-d3c81c41-db-1  0.0.0.0:20002->5432/tcp, [::]:20002->5432/tcp
wtenv-feature-x-d3c81c41-backend-1  0.0.0.0:20001->6379/tcp, [::]:20001->6379/tcp
```

`post_up` commands get the same isolation as `wtenv exec`. `wtenv ls --json` and the JSON of
`up` carry `compose_isolation` (`"exec_only"`, `"automatic"`, or null without compose) and
`compose_repository_override`.

**A plain `docker compose` in such a worktree runs without isolation.** It uses the repository's
setup as it is: the repository's fixed ports, and a project name Compose derives from the
directory, which wtenv does not record and `wtenv down` never removes. It can collide with the
main checkout's stack when both directories have the same name. A command's own `-p` or `-f`
wins over the isolation file.

```text
$ docker compose config          # plain: not isolated
project: feature-x
backend published: 8000
$ wtenv exec -- docker compose config
project: wtenv-feature-x-d3c81c41
backend published: 20001
```

(Each of the two commands there was run with `--format json` and read for the project name and
the published port of `backend`.)

If the repository has two override files that Compose loads automatically, `up` stops with
`override_ambiguous` (exit 21), names both, and changes nothing. If a file of yours sits at the
path wtenv recorded as its own override and git tracks it, `up` treats it as your repository's
(`compose_override_adopted`) and `down` keeps it (`kept_items` in the JSON, reason
`repository_owned`); if git does not track it, `up` fails with `ownership_conflict` as before.

When `wtenv exec` cannot apply the isolation settings it fails with `isolation_unavailable` and
exit status 125, and does not run the command:

```text
$ COMPOSE_PROJECT_NAME=other wtenv exec -- true
wtenv: error [isolation_unavailable]: COMPOSE_PROJECT_NAME is set in the environment, so Compose would not use the isolation file; the command was not run
hint: Fix that, or run `wtenv up` in this worktree, then run the command again.
```

<a id="isolation_unavailable"></a>`error.details.reason` says which check failed:

| `reason` | What happened | Fix |
|----------|---------------|-----|
| `compose_env_override` | `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` or `COMPOSE_ENV_FILES` is set in the environment (the first two also in the `.env` beside the compose file), or `COMPOSE_FILE` names another worktree's isolation file | Unset it (`details.variable`, and `details.path` for a `.env` or the other file) |
| `recorded_value_invalid` | The registry holds a value `up` never records | Run `wtenv up` |
| `symlink` | `.wtenv/` or the isolation file is a symbolic link | Remove the link, then run `wtenv up` |
| `isolation_file_missing` | `.wtenv/compose.isolation.yaml` is gone | Run `wtenv up` |
| `isolation_file_not_wtenvs` | The file no longer starts with wtenv's header line | Move your file away, then run `wtenv up` |
| `repository_override_missing` | The repository's override recorded for the worktree is gone | Restore it, or run `wtenv up` to return to the automatic arrangement |
| `override_appeared` | Another override file with an auto-loaded name appeared beside the compose file | Run `wtenv up`: it switches to your override, or stops with `override_ambiguous` when there are two |

In a worktree without your own override, `exec` behaves as before, with two exceptions that give
the same error: `COMPOSE_FILE` naming another worktree's isolation file, and an override file
that appeared since `up` beside wtenv's own.

### Port variables

With `[compose]`, `up` writes one variable for every published port of the Compose setup, next to
your `ports` variables, in the env file and in `wtenv exec`:

```text
# >>> wtenv managed (rewritten by `wtenv up`; do not edit) >>>
PORT=20000
WTENV_PORT_BACKEND_6379_TCP=20001
WTENV_PORT_DB_5432_TCP=20002
DATABASE_URL=postgresql://postgres:p%40ss%2Fw%3Ard@localhost:20002/postgres
# <<< wtenv managed <<<
```

The name is `WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>`. `<SERVICE>` is the service name in
upper case with every character other than `A-Z` and `0-9` replaced by `_`, `<CONTAINER PORT>` is
the container port in decimal, and `<PROTOCOL>` is `TCP` or `UDP`. The value is the host port
assigned to that published port. A port tied to a `ports` variable has both variables, with the
same number. When two names would be the same, or a name equals a variable you defined, `up` stops
with `port_variable_conflict` (exit 22) and changes nothing:

```text
$ wtenv up
wtenv: error [port_variable_conflict]: service db, container port 5432/tcp and wtenv.toml ports would both set the variable WTENV_PORT_DB_5432_TCP
hint: Rename a service or a `ports` entry, or publish the container port once.
```

The port variables and `DATABASE_URL` are **host-side values**. A container that receives one
through Compose interpolation (`${WTENV_PORT_DB_5432_TCP}`) gets the host-side value, not the
container port.

### A database that runs as a Compose service

With `type = "compose"`, the database is a service of the worktree's own Compose project, so
each worktree has its own database and its own data volume. wtenv creates nothing for it and
never connects to it: `up` finds the service's one published port and writes `DATABASE_URL` for
the port it was assigned.

```toml
ports = ["PORT"]

[compose]
file = "compose.yaml"

[database]
type = "compose"
service = "db"
url = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/postgres"
```

The service must exist in the compose file (combined with your override) and publish exactly one
port; otherwise `up` fails with `config_invalid` (`setting` `database.service`). The URL must use
`postgresql://` or `postgres://`, name the host explicitly as `localhost`, `127.0.0.1` or `[::1]`,
have `{port}` as the port, and have a database name. `{compose_env:NAME}` is percent-encoded, so
a password holding `@`, `/`, `:` or `%` stays a password: `p@ss/w:rd` becomes `p%40ss%2Fw%3Ard` in
the URL above. Passwords stay out of `wtenv.toml`, the registry and every output of wtenv, and are
written only to `DATABASE_URL` in the env file and `wtenv exec`.

`up` refuses data storage that other worktrees or the main checkout would share, before it creates
anything, with `database_storage_shared` (exit 23). Every writable mount of the database service
counts as data (mark the others `:ro`):

```text
$ wtenv up
wtenv: error [database_storage_shared]: service db keeps its data where other worktrees or the main checkout would share it (fixed_name_volume: shared-pgdata)
hint: Store the data in a project volume (no `name:`, not `external`) or in a directory inside the worktree. Nothing was changed.
```

`details.storage` is `fixed_name_volume` (a `name:` on the volume), `external_volume`,
`device_not_absolute` (an `nfs`, `cifs` or local-bind volume whose `device` is not an absolute
path), or `host_directory` (a bind mount outside the worktree, judged by its real path). Fix it
by storing the data in a project volume (no `name:`, not `external`) or in a directory inside the
worktree. An anonymous volume, no volume, and a directory inside the worktree are accepted with
the warning `compose_database_data_kept`: `down` and `gc` keep that data.

### `.env` files and the exclude block

Compose reads `.env` from the directory a command runs from (Compose 2.24.4: instead of the
project directory's; Compose 5.1.4: as well as it, and its values win), and any file named by
`COMPOSE_ENV_FILES` or `--env-file`. Each can set `COMPOSE_PROJECT_NAME` and so move the stack to
another project. Through `wtenv exec` and `post_up` commands in a worktree whose repository owns
its override, Compose reads only the `.env` beside the compose file (`COMPOSE_ENV_FILES`), and an
inherited `COMPOSE_ENV_FILES` makes `exec` fail. Anywhere else, run Compose from the compose
file's directory; a `.env` in another directory can change the project name and wtenv does not
detect it.

wtenv's lines in `.git/info/exclude` are shared by every worktree of the repository. An untracked
override of your own can therefore be ignored by git because of a line wtenv wrote for another
worktree; then a checkout or `git clean -X` can overwrite or delete it. `up` warns
`compose_override_excluded` when it finds that.

### Downgrading to v0.1.x

Before downgrading to v0.1.x, run `wtenv down` in every worktree whose repository owns its
override. Otherwise the registry, one file for every repository on the machine, becomes
unreadable to v0.1.x (`registry_unreadable`), and the `post-checkout` hook then fails silently.

### Changing the configuration later

The next `up` applies the change: a new `ports` list reassigns ports inside the same block; a
new `block_size` moves the worktree to a new block; a new `env_file` moves the section and
removes it from the old file; a changed `database.url` rewrites `DATABASE_URL` and keeps the
existing database (it is never re-created from a new template); removing `[database]` or
`[compose]` stops managing them but keeps what exists recorded until `down`.

## Working with Claude Code

Claude Code switches git hooks off when it creates a worktree (`claude --worktree`), so the
`post-checkout` hook does not provision worktrees Claude Code makes. Add this line to your
project's `CLAUDE.md`:

```text
Before starting servers or running migrations in a new worktree, run `wtenv up` in it.
```

`wtenv exec` fails with `not_provisioned` and a hint naming `wtenv up` if an agent forgets.

Do not list wtenv's env file in `.worktreeinclude`: Claude Code would copy the main
checkout's file, with its ports, into each new worktree until `wtenv up` rewrites it.

Worktrees that Claude Code removes become `orphaned`, and `wtenv gc` reclaims them. Run `gc`
after removal: git has no hook for worktree removal, so nothing can run `down` for you.

For worktrees you create yourself with `git worktree add`, run `wtenv hook install` once per
clone and the hook provisions each new worktree.

## Known limits

wtenv v1 prefers to refuse than to guess. These are the limits you can hit.

**Cleanup**

- **Anonymous volumes stay on disk.** Docker gives the compose project label only to named
  volumes, so an anonymous volume of a removed project cannot be tied to it. `down` and `gc`
  never remove one; they list it under `kept_volumes`. Remove it yourself with
  `docker volume rm NAME` once you are sure it holds nothing you need.
- **A volume with a fixed `name:` stays too**, because the main checkout and every worktree
  share it. `up` warns about it (`compose_fixed_volume_name`). That warning fires only for a
  volume a service mounts: Compose leaves unused top-level volumes out of the resolved model
  wtenv reads, so an unused one is neither warned about nor listed.
- **A tracked file at the override path is kept, not removed.** When git tracks the file at the
  path wtenv recorded as its override, `down` leaves it alone and lists it under `kept_items` in
  the JSON (reason `repository_owned`); the text output does not list it. `gc` and
  `gc --release` run on a path that is no longer a git worktree, so git cannot say whether a file
  is tracked: a file without wtenv's header is left alone and reported under `failed`, but a
  tracked file that still carries wtenv's header is removed, as in v0.1.1.
- Nothing is removed unless wtenv recorded creating it. A database or file that merely has a
  `wtenv_` name is left alone.

**Compose**

- One compose file per worktree, named `compose.yaml`, `compose.yml`, `docker-compose.yaml`,
  or `docker-compose.yml`. Those are the only names plain `docker compose` finds, and wtenv
  adds no flags.
- **Your own override is isolated only through `wtenv exec`.** In a repository that tracks its
  own override, a plain `docker compose` runs without isolation (see
  [A repository that owns its override](#a-repository-that-owns-its-override)). A command's own
  `-p` or `-f` wins over the isolation file.
- A file at the override name wtenv recorded as its own, that has no wtenv header and that git
  does not track, is `ownership_conflict`: wtenv never rewrites or deletes a file it did not
  write.
- `COMPOSE_PROJECT_NAME` or `COMPOSE_FILE` set in the environment or in `.env` beside the
  compose file beats the override, so `up` fails with `unsupported` instead of silently not
  isolating. `.env` lines are read in the forms Compose reads: `KEY=value` and `KEY: value`, with
  or without `export`, after an optional byte-order mark.
- Compose 2.24.4 reads `.env` from the current directory only; run Compose from the compose
  file's directory there. See [`.env` files and the exclude block](#env-files-and-the-exclude-block).
- **Three setups that v0.1.1 accepted now fail with `port_variable_conflict` (exit 22):** one
  container port published twice (for example an override that adds a second host port for the
  same container port), two service names that map to one variable name (`my-api` and `my_api`,
  or `API` and `api`), and a `ports` entry named like a `WTENV_PORT_…` variable.
- Every writable mount of a compose-hosted database service counts as data. Mark the others
  `:ro`.
- A fixed `image:` tag is the same in every worktree, so one worktree's build replaces the tag
  another one runs.
- `isolation_unavailable` has seven reasons, each with its fix, in
  [the table above](#isolation_unavailable); the exit statuses 20 to 23 are in the status table.
- A host port range published onto one container port (`"8000-9000:80"`) is `unsupported`:
  Compose picks the port at start, so it cannot be pinned.
- Compose 2.24.4 or later is required (`!override`).

**Databases**

- Postgres over local TCP only: the host must be `localhost`, `127.0.0.1`, or `[::1]`.
  Unix-socket URLs and remote servers are refused.
- Postgres cannot copy a database with open connections. If something is connected to the
  template, `up` fails at once with `template_in_use` and changes nothing.
- A SQLite template with a non-empty write-ahead log (`-wal`) file is refused for the same
  reason.

**Platform and git**

- macOS and Linux (WSL2 counts). No native Windows.
- Ports are tested for TCP only.
- The hook runs only for a plain `git worktree add`. `--no-checkout` and `--orphan` run no
  `post-checkout` hook, hooks are not cloned (install once per clone), and a caller can
  switch them off. If `core.hooksPath` points elsewhere, `hook install` refuses and prints the
  block for you to add to your hook manager.
- A worktree you deleted by hand while git still lists it is kept until `git worktree prune`.
  A deleted repository is kept until you name it with `gc --release PATH`.

## wtenv and the alternatives

wtenv does one narrow job. It is not a replacement for the tools below, and for some
situations they are the better choice.

**Dev containers.** A dev container puts your whole toolchain and your code in a container, so
every developer and every worktree starts from the same environment. wtenv does not do that:
your code and tools run on your machine as they do today, and wtenv only separates the
runtime resources they use (ports, database, compose project, env file). That makes it light,
with no container to build and no editor integration, and it also means it cannot give you a
reproducible toolchain or isolate the filesystem. If you need those, use a dev container.
Each dev container still needs its own answer to "which port, which database", which is the
part wtenv covers.

**Hand-rolled scripts.** A shell script or Makefile target that picks a port and runs
`createdb` is quick to write and does exactly what you tell it. If your need is one port and
one SQLite file, it may be all you need. What it usually lacks is what wtenv adds: ports that
cannot collide when two agents start at the same moment, a record of what was created so
`down` removes that and nothing else, cleanup after a worktree is deleted without running
`down`, a dry run, and the same exit codes and JSON every time. The price is that wtenv
supports only what is listed above; a script can do anything.

**Cloud preview environments.** A preview environment gives each branch or pull request a
full, production-like deployment with a shareable URL, and it costs your machine nothing. It
needs a push, a build, and time to deploy, and it fits review better than the edit-and-run
loop of an agent working on uncommitted code. wtenv is local: free, immediate, and nothing
leaves your machine, but it cannot share a running environment with a teammate and it is not
production-like.

## License

Apache License 2.0. See [LICENSE](LICENSE).
