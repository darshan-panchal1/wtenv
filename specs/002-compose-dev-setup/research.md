# Research: Compose dev setups (spec 002)

**Feature**: `002-compose-dev-setup` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

Every finding below comes from an experiment run on 2026-10-10 against **two** Compose builds on
the same Docker engine (29.5.3, Docker Desktop, macOS arm64):

| Build | How it was installed | Role |
|-------|----------------------|------|
| Docker Compose **v2.24.4** | Official release `docker-compose-darwin-aarch64`, SHA-256 `e5d7dd8f…09d9` checked against the release's `checksums.txt`, installed as a CLI plugin in a separate `DOCKER_CONFIG` | The supported minimum and the CI `minimums` job |
| Docker Compose **v5.1.4** | Docker Desktop's bundled plugin | The newest build installed here |

The harness is in [research/](research/): `run.sh` (E1 to E5), `e2b.sh` (E2.13 to E2.15),
`e1c.sh` (E1.6) and `e5_rule.py` (E5). The full logs are `research/out-*.txt`. In them, `<W>`
stands for the throwaway work directory and `<S>` for the scratch directory. Each command line
in the logs is followed by its output and by `[exit N]`, which is Compose's own exit status
(the pipeline runs with `pipefail`).

Every project, container, network and volume the experiments created was named
`wtenv-research-*` and was removed at the end. `docker ps -a`, `docker volume ls` and
`docker network ls` were compared with a baseline saved before the first experiment: no
leftovers (see the plan's completion report).

The two builds agree on every result that decides a design question. The differences are
listed in each section; none of them changes a decision.

---

## R1. How `ports` merge across files; `!override` and `!reset` at 2.24.4 (FR-003)

**Question.** The repository's override is a second compose file. wtenv's isolation settings
have to sit on top of it. Does a third file with a `ports:` list replace the list or add to it?
Do `!override` and `!reset` work at 2.24.4?

**Decision.**

- In a worktree whose repository owns its override, wtenv's isolation settings are a **third
  compose file**, loaded after the compose file and the repository's override. It keeps the
  exact layout of the v0.1.x generated override (files.md): `name:` plus, for every service
  that publishes ports, `ports: !override` and the full list.
- The list wtenv writes is computed from the **combined** model of the compose file and the
  repository's override, so a port that only the repository's override declares is remapped
  too, and every field Compose reported for an entry (`host_ip`, `protocol`, `mode`, `name`,
  `app_protocol`) is repeated with only `published` changed (FR-003).
- `!reset` is not used.
- The minimum Compose version stays **2.24.4**. The spec does not need to change.

**Rationale.**

- A plain `ports:` list in a later file is **appended** (E1.1, E1.2). The reviewer's caution is
  confirmed on 2.24.4: a plain third file leaves both the repository's fixed ports and wtenv's
  ports published. The repository override's `127.0.0.1:18002` stayed beside wtenv's `28001`.
- `ports: !override` in the third file **replaces** the merged list of the first two files, on
  2.24.4 as on 5.1.4 (E1.3). The repository override's other settings (`command`) are kept.
  At run time the containers publish only wtenv's ports (E1.5).
- `!reset []` empties the list (E1.4). It cannot also set a new list in the same key, so it
  would need a second mechanism to publish wtenv's ports. `!override` does both in one key.
- The last file's `name:` wins over a `name:` in the repository's override, and a host port
  taken from a variable in the repository's override is replaced as well (E1.4b).
- Identical port entries in the two files are merged into one. The same container port on
  **another** host port is appended as a second entry (E1.6). This matters for R5.

**Alternatives considered.**

- *A plain third file.* Rejected: the ports are appended (E1.2), so isolation would not hold.
- *`!reset` plus another key.* Rejected: two mechanisms for one result, and 001 research §4
  already rejected it for the same reason.
- *Editing or regenerating the repository's override.* Forbidden by FR-002.

**Evidence** (`research/out-compose-2.24.4.txt`; the 5.1.4 log gives the same lines):

```text
## E1.1 compose.yaml + repository override, auto-loaded (no flags)
$ cd <W>/wtenv-research-e1 && docker compose config --format json | ports
[{"s":"api","p":[{"target":6379,"published":"18003",...}]},{"s":"web","p":[{"target":6379,"published":"18001","host_ip":null,...},{"target":6379,"published":"18002","host_ip":"127.0.0.1",...}]}]
[exit 0]

## E1.2 + a third file with a plain ports: list
$ ... docker compose -f compose.yaml -f compose.override.yaml -f .wtenv/plain.yaml config --format json | ports
[... {"s":"web","p":[{"published":"18001"},{"published":"18002","host_ip":"127.0.0.1"},{"published":"28001"}]}]   # appended
[exit 0]

## E1.3 + a third file with ports: !override (wtenv's generated layout)
$ ... -f .wtenv/override.yaml config --format json | jq -c '{name, ports: ..., web_command: .services.web.command}'
{"name":"wtenv-research-e1-iso","ports":[{"s":"api","p":[{"target":6379,"published":"28003","host_ip":null}]},{"s":"web","p":[{"target":6379,"published":"28001","host_ip":null},{"target":6379,"published":"28002","host_ip":"127.0.0.1"}]}],"web_command":["valkey-server","--port","6379","--save",""]}
[exit 0]

## E1.4 + a third file with ports: !reset []
[{"s":"api","p":[{"target":6379,"published":"18003",...}]},{"s":"web","p":[]}]
[exit 0]

## E1.4b the repository's override sets its own name: and a host port from a variable
$ ... docker compose -f compose.yaml -f .wtenv/repo-named.yaml config --format json | jq -c '{name, web: ...}'
{"name":"wtenv-research-e1-repo","web":["18001","18002"]}
$ WEB_PORT=18044 docker compose -f compose.yaml -f .wtenv/repo-named.yaml -f .wtenv/override.yaml config ...
{"name":"wtenv-research-e1-iso","web":["28001","28002"]}

## E1.5 runtime: up -d with the three files, then the published ports of each container
$ docker ps --filter label=com.docker.compose.project=wtenv-research-e1-iso --format '{{.Names}} {{.Ports}}' | sort
wtenv-research-e1-iso-api-1 0.0.0.0:28003->6379/tcp, [::]:28003->6379/tcp
wtenv-research-e1-iso-web-1 0.0.0.0:28001->6379/tcp, 127.0.0.1:28002->6379/tcp, [::]:28001->6379/tcp
```

```text
## E1.6a the override repeats the compose file's entry exactly          (research/out-e1c.txt)
[{"target":6379,"published":"18001"}]                                   # 2.24.4 and 5.1.4
## E1.6b the override publishes the same container port on another host port
[{"target":6379,"published":"18001"},{"target":6379,"published":"18002"}]
```

Differences between the builds: only the progress lines of `up` and `down`.

---

## R2. Applying the isolation file to `wtenv exec` and post-up commands (FR-004, FR-033)

**Question.** How can Compose commands that `wtenv exec` (or a post-up command) starts, directly
or through a child process, load the compose file, the repository's override and wtenv's
isolation file, without touching tracked files? What happens when the child passes `-p`, `-f`
or `COMPOSE_PROJECT_NAME`?

**Decision.**

- `exec` and the post-up commands set **`COMPOSE_FILE`** to the three files as **absolute
  paths**, in this order: the compose file, the repository's override, the isolation file. They
  also set **`COMPOSE_PATH_SEPARATOR=:`** so that a separator inherited from the developer's
  environment cannot split the list differently.
- They do **not** set `COMPOSE_PROJECT_NAME`. The project name comes from the isolation file's
  `name:`. A child that passes its own `-f` therefore leaves wtenv's project entirely, as the
  spec says ("the command's own choice wins, so it is not isolated"). It does not start the
  developer's own files under wtenv's project label, which `wtenv down` would later remove.
- `up` verifies the isolation file by running `docker compose --profile "*" config --format
  json` with **the same environment builder** that `exec` uses (one function), from the compose
  file's directory. The project name must be the recorded one and the published ports must be
  exactly the assigned ones. Otherwise `up` removes the file it just wrote and fails with
  `unsupported` (`compose_verification_failed`), as in v0.1.x.
- `up` refuses, with `unsupported` (new reason `compose_path_separator`), a worktree in that
  mode whose compose file, repository override or isolation file has an absolute path that
  contains `:`. Such a list cannot be written to `COMPOSE_FILE` (see E2.10).
- `exec` checks, before it runs anything, every condition FR-033 lists. When one fails it exits
  125 with the new error code `isolation_unavailable` and does not run the command
  ([contracts/cli.md](contracts/cli.md)).

**Rationale.**

- `COMPOSE_FILE` with three absolute paths isolates the stack from the compose file's
  directory, from a subdirectory, from a directory outside the project, and through nested
  `sh -c` (E2.2, E2.3, E2.4). Relative paths break as soon as the child changes directory
  (E2.5).
- A plain `docker compose` with no `COMPOSE_FILE` in that worktree loads only the compose file
  and the repository's override: the repository's project name and its fixed ports (E2.1). This
  is the FR-004 relaxation, and it is what `up` must say.
- The child's `-p` wins over `name:` while the ports stay remapped (E2.6). The child's `-f`
  makes Compose ignore `COMPOSE_FILE` entirely (E2.7). `COMPOSE_PROJECT_NAME` beats `name:`
  (E2.8), which is why `exec` refuses to run when the caller's environment sets it (FR-033).
- `COMPOSE_FILE` in the environment beats `COMPOSE_FILE` in `.env` (E2.9). `up` still refuses
  both, as in v0.1.x (spec, Edge cases).
- `COMPOSE_PATH_SEPARATOR` is honoured by both builds: with `;` inherited, a `:`-joined list is
  read as one missing file and Compose fails (E2.10). Setting it to `:` makes the list
  unambiguous.
- A deleted isolation file makes Compose fail with "no such file or directory" (E2.12), so a
  missing file can never be silently skipped. `exec` checks for it anyway, to give the stable
  error FR-033 asks for and to not run the command at all.
- `--env-file` and `COMPOSE_ENV_FILES` only feed interpolation. They cannot add a compose file
  or change the project name (E2.11).

**Known limit, observed and not caused by wtenv.** Compose 2.24.4 reads `.env` from the
**current directory**, Compose 5.1.4 from the project directory. A Compose command started from
a subdirectory therefore misses the repository's `.env` interpolation values on 2.24.4: with
`COMPOSE_FILE` (E2.3), with `COMPOSE_PROJECT_DIRECTORY` (E2.13, ignored for `.env` on 2.24.4),
and equally with no wtenv involved at all (E2.15). `--project-directory` fixes it (E2.14) but is
a command-line flag that `exec` cannot inject. The project name and the ports are not affected:
they come from the files, not from `.env`. The user documentation states the limit. wtenv does
not set `COMPOSE_PROJECT_DIRECTORY`.

**Alternatives considered.**

- *`COMPOSE_PROJECT_NAME` set by `exec`.* Rejected: a child with its own `-f` would start other
  files under wtenv's project, and `down` would remove their containers and `<project>_`
  volumes.
- *An env file read by Compose (`--env-file`, `COMPOSE_ENV_FILES`).* Rejected: it cannot add a
  compose file (E2.11).
- *Writing `COMPOSE_FILE` to the tracked `.env`.* Forbidden: 001:FR-018 and FR-002.
- *A wrapper `docker` script on `PATH`.* Rejected: invisible magic, breaks absolute calls, and
  is outside the spec.

**Evidence** (`research/out-compose-2.24.4.txt`, `research/out-e2b.txt`; `CF` is the three
absolute paths joined with `:`):

```text
## E2.1 no flags, no COMPOSE_FILE (a plain command in the worktree: not isolated)
{"name":"wtenv-research-e2-base","web":["18001","18002"],"api":["18003"],"dotenv":"from-dot-env"}
## E2.2 COMPOSE_FILE with three absolute paths, from the compose file's directory
{"name":"wtenv-research-e2-iso","web":["28001","28002"],"api":["28003"],"dotenv":"from-dot-env"}
## E2.3 the same from a subdirectory, and from a directory outside the project
{"name":"wtenv-research-e2-iso","web":["28001","28002"],"api":["28003"],"dotenv":"unset"}   # 2.24.4
{"name":"wtenv-research-e2-iso","web":["28001","28002"],"api":["28003"],"dotenv":"from-dot-env"}   # 5.1.4
## E2.4 through a child shell (sh -c), as a script or make target would run it
$ COMPOSE_FILE=$CF sh -c 'sh -c "docker compose config --format json"' | ...
{"name":"wtenv-research-e2-iso","web":["28001","28002"],"api":["28003"],"dotenv":"from-dot-env"}
## E2.5 relative COMPOSE_FILE paths from a subdirectory
stat <W>/wtenv-research-e2/backend/compose.yaml: no such file or directory
[exit 1]
## E2.6 the child passes -p: its project name wins, the ports stay remapped
{"name":"wtenv-research-e2-child","web":["28001","28002"],"api":["28003"],"dotenv":"from-dot-env"}
## E2.7 the child passes -f: COMPOSE_FILE is ignored entirely
{"name":"wtenv-research-e2-base","web":["18001"],"services":["web"]}
## E2.8 COMPOSE_PROJECT_NAME in the environment beside COMPOSE_FILE
{"name":"wtenv-research-e2-env","web":["28001","28002"],"api":["28003"],"dotenv":"from-dot-env"}
## E2.9 COMPOSE_FILE in .env, and the environment's COMPOSE_FILE beside it
{"name":"wtenv-research-e2-base","web":["18001"],"services":["web"]}                 # .env only
{"name":"wtenv-research-e2-iso","web":["28001","28002"],"api":["28003"],...}           # environment wins
## E2.10 COMPOSE_PATH_SEPARATOR
$ COMPOSE_PATH_SEPARATOR=';' COMPOSE_FILE=$CF docker compose config ...
stat <W>/wtenv-research-e2/compose.yaml:<W>/...override.yaml:<W>/.../.wtenv/override.yaml: no such file or directory
$ COMPOSE_PATH_SEPARATOR=';' COMPOSE_FILE='<the same paths joined with ;>' ...   -> isolated
$ COMPOSE_PATH_SEPARATOR=':' COMPOSE_FILE=$CF ...                                -> isolated
## E2.11 --env-file and COMPOSE_ENV_FILES only feed interpolation
{"name":"wtenv-research-e2-base","web":["18001","18002"],"api":["18003"],"dotenv":"from-env-file"}
## E2.12 a missing file in COMPOSE_FILE (isolation file deleted)
stat <W>/wtenv-research-e2/.wtenv/missing.yaml: no such file or directory
## E2.13 COMPOSE_PROJECT_DIRECTORY beside COMPOSE_FILE, from a subdirectory
2.24.4: {"name":"wtenv-research-e2-iso",...,"dotenv":"unset"}    5.1.4: {...,"dotenv":"from-dot-env"}
## E2.14 --project-directory flag, from a subdirectory
2.24.4: {...,"dotenv":"from-dot-env"}                             5.1.4: {...,"dotenv":"from-dot-env"}
## E2.15 no COMPOSE_FILE, plain command from a subdirectory
2.24.4: {"name":"wtenv-research-e2-base",...,"dotenv":"unset"}   5.1.4: {...,"dotenv":"from-dot-env"}
```

Differences between the builds: the `.env` lookup from another directory (E2.3, E2.13, E2.15),
described above.

---

## R3. What `docker compose config` exposes (FR-011, FR-021, FR-026, FR-027)

**Question.** Does `config --format json` give each service's published ports and volumes after
interpolation, including values from the tracked `.env` and `name:` lines, so that every
decision of Stories 2 and 3 can be made from it?

**Decision.**

- Every new decision reads one document: `docker compose -f <compose file> [-f <repository
  override>] --profile "*" config --format json`, run from the compose file's directory with
  the port-variable markers in the environment (the v0.1.x resolution command, with the
  repository's override added when there is one).
- Published ports: `services.<s>.ports[]` (`target`, `published`, `protocol`, `host_ip`,
  `mode`), as v0.1.x already reads them.
- Mounts: `services.<s>.volumes[]` (`type`, `source`, `target`, `read_only`) and
  `services.<s>.tmpfs`; top-level `volumes.<key>` (`name`, `external`, `driver_opts`); top-level
  `name`.
- Credentials for a compose-hosted database: the **database service's resolved
  `environment`**, which already holds the values of its `env_file` and of `.env`
  interpolation. The URL pattern reads it through a new placeholder `{compose_env:NAME}`
  (data-model.md, contracts/config.md). The values stay in memory: they go into
  `DATABASE_URL` in the env file and nowhere else (FR-020).
- `docker compose config --environment` is not used: it does not exist on 2.24.4 (E3.4).

**Rationale.**

- Interpolated values from the tracked `.env` appear resolved: `"${APP_PORT}:6379"` gives
  `published: "18005"`, a volume `app-${VOL_SUFFIX}` gives `source: "app-data"` (E3.1).
- Short syntax is expanded. `"127.0.0.1::6380"` and `"6381"` have no `published` key, and
  `"18010:53/udp"` has `protocol: "udp"` (E3.1).
- `name:` from the repository's override is the model's `name`, and with no `name:` at all the
  model reports the default (the directory name). Volumes resolve to `<name>_<key>` in both
  cases (E3.1, E3.2). This is what the 001 reading R8 test compares against.
- A service's `env_file` is merged into its `environment` in the output (`env_file` itself is
  null), and an `environment` entry interpolated from `.env` holds the value (E3.1). On the
  fastapi template the `db` service's environment holds `POSTGRES_PASSWORD` and `POSTGRES_DB`
  (E5.1), so the template needs no extra export (spec, Assumptions).
- The process environment beats `.env` in interpolation (E3.3). This is the documented FR-025
  effect: a variable `exec` sets is what Compose interpolates.

**Alternatives considered.** Parsing YAML or `.env` in wtenv: rejected in 001 research §4 for
the same reasons (a dependency, and it would disagree with Compose). Reading the interpolation
environment with `config --environment`: not available on 2.24.4.

**Evidence** (`research/out-compose-2.24.4.txt`):

```text
## E3.1 ports, volumes, name and environment after interpolation from the tracked .env
$ cd <W>/wtenv-research-e3 && docker compose -f compose.yaml -f compose.override.yaml --profile '*' config --format json | jq -c '{...}'
{"name":"wtenv-research-e3-from-override",
 "ports":[{"mode":"ingress","target":6379,"published":"18005","protocol":"tcp"},
          {"mode":"ingress","host_ip":"127.0.0.1","target":6380,"protocol":"tcp"},
          {"mode":"ingress","target":6381,"protocol":"tcp"},
          {"mode":"ingress","target":53,"published":"18010","protocol":"udp"}],
 "app_volumes":[{"type":"volume","source":"app-data","target":"/data","volume":{}},
                {"type":"bind","source":"<W>/wtenv-research-e3/cache","target":"/cache","bind":{"create_host_path":true}}],
 "volumes":{"app-data":{"name":"wtenv-research-e3-from-override_app-data"}},
 "db_env_keys":["APP_PORT","DB_PASSWORD","POSTGRES_PASSWORD","VOL_SUFFIX"],
 "db_password":"from-tracked-dot-env","db_env_file":null}
## E3.2 with no name: anywhere, config reports the default name
{"name":"wtenv-research-e3","volumes":{"app-data":{"name":"wtenv-research-e3_app-data"}}}
## E3.3 the environment beats .env in interpolation
["18099"]
## E3.4 docker compose config --environment
unknown flag: --environment          [exit 16]    # 2.24.4; 5.1.4 prints the variables
```

Differences between the builds: 5.1.4 prints `"bind":{}` where 2.24.4 prints
`"bind":{"create_host_path":true}`, and only 5.1.4 has `config --environment`. wtenv reads
neither.

---

## R4. Where a database service keeps its data (FR-026, FR-027)

**Question.** Using only Compose's resolved model and the path rules of 001 reading R8, can
wtenv tell an anonymous volume, a fixed-name volume, an external volume and a host directory
apart for the database service?

**Decision.** The **data mounts** of the database service are its entries in
`services.<db>.volumes` of `type` `volume` or `bind` that are not `read_only`. `tmpfs` entries
and `read_only` mounts are not data mounts. Each data mount is classified from the resolved
model alone:

| Resolved mount | Class | `up` |
|----------------|-------|------|
| `type: volume`, no `source` | anonymous volume | accepts, warns `compose_database_data_kept` (`anonymous_volume`) (FR-026) |
| `type: volume`, `source: K`, `volumes.K.external` true | external volume | refuses, `database_storage_shared` (`external_volume`) (FR-027) |
| `type: volume`, `source: K`, `volumes.K.name` is not `<model name>_K` | fixed-name volume (reading R8) | refuses, `database_storage_shared` (`fixed_name_volume`) |
| `type: volume`, `source: K`, `volumes.K.driver_opts.device` set | host directory behind a volume | judged as a `bind` with that path (next two rows) |
| `type: volume`, `source: K`, name `<model name>_K`, no `device` | project-scoped volume | accepts silently |
| `type: bind`, real path of `source` inside the worktree's real root | host directory inside the worktree | accepts, warns `compose_database_data_kept` (`worktree_directory`) (FR-027) |
| `type: bind`, real path of `source` outside the worktree's real root | host directory outside the worktree | refuses, `database_storage_shared` (`host_directory`) |

A database service with **no** data mount at all keeps its data in whatever volume its image
declares (an anonymous volume) or in the container: `up` accepts it and warns
`compose_database_data_kept` (`no_volume`). That is the spec's "anonymous volume" edge case
("the service declares no volume and the image declares one"). wtenv does not inspect the
image: `up` does not pull images (001:FR-034), and the compose model is the only input the user
asked for.

"Inside the worktree" is decided on real paths: `os.path.realpath(source)` must equal the
worktree root's real path or start with it followed by `/` (spec, Assumptions: "by its real
path, after following symbolic links").

**Rationale.**

- The four storage kinds are told apart by the resolved model (E4.1): an anonymous volume has
  no `source`; an external volume has `external: true`; a fixed-name volume has a `name` other
  than `<model name>_<key>` (`wtenv-research-e4-shared` beside `wtenv-research-e4_pgdata`); a
  bind has an absolute `source`.
- A named volume with `driver_opts.device` gets a project-scoped name
  (`wtenv-research-e4_device`) but stores its data in a fixed host directory (E4.1). The R8 name
  test alone would accept it, and every worktree would share that directory. It is therefore
  judged by its device path.
- A `read_only` bind (`/etc/localtime:/etc/localtime:ro`) is reported with `read_only: true`
  (E4.1). A database cannot write data there, so it must not trigger a refusal.
- An image-declared volume is not in the model: `db_none` has `volumes: null` (E4.1), yet the
  image declares `VOLUME /var/lib/postgresql/data` (E4.2) and at run time the container mounts
  an anonymous volume that carries **no** compose label (E4.3). `down` and `gc` keep it, as 001
  reading R6 says, so the warning is accurate.
- Treating every writable mount as a data mount errs on the safe side: a writable bind outside
  the worktree on the database service is refused, with the path named, even if it is not the
  data directory. The developer can mark it `:ro`. This avoids needing the image's `PGDATA`.

**Alternatives considered.**

- *Only the mount whose target covers `PGDATA`.* Rejected: `PGDATA` comes from the image when
  the service does not set it, and reading it needs `docker image inspect` on an image that may
  not be pulled yet.
- *`docker image inspect` for the image's `VOLUME`s.* Rejected for the same reason, and it is
  not needed: no data mount already tells wtenv the data is not in a project volume.

**Evidence** (`research/out-compose-2.24.4.txt`):

```text
## E4.1 the resolved mounts of each database service, and the top-level volumes
{"s":"db_anon_declared","volumes":[{"type":"volume","target":"/var/lib/postgresql/data","volume":{}}],"tmpfs":null}
{"s":"db_bind_inside","volumes":[{"type":"bind","source":"<W>/wtenv-research-e4/data","target":"/var/lib/postgresql/data",...}],"tmpfs":null}
{"s":"db_bind_outside","volumes":[{"type":"bind","source":"<W>/outside-data","target":"/var/lib/postgresql/data",...},
                                   {"type":"bind","source":"/etc/localtime","target":"/etc/localtime","read_only":true,...}],"tmpfs":null}
{"s":"db_device","volumes":[{"type":"volume","source":"device","target":"/var/lib/postgresql/data","volume":{}}],"tmpfs":null}
{"s":"db_external","volumes":[{"type":"volume","source":"external",...}],"tmpfs":null}
{"s":"db_fixed","volumes":[{"type":"volume","source":"fixed",...}],"tmpfs":null}
{"s":"db_none","volumes":null,"tmpfs":null}
{"s":"db_project","volumes":[{"type":"volume","source":"pgdata",...}],"tmpfs":null}
{"s":"db_tmpfs","volumes":null,"tmpfs":["/var/lib/postgresql/data"]}
$ docker compose config --format json | jq -c '{name, volumes}'
{"name":"wtenv-research-e4","volumes":{
  "device":{"name":"wtenv-research-e4_device","driver_opts":{"device":"<W>/outside-data","o":"bind","type":"none"}},
  "external":{"name":"wtenv-research-e4-external","external":true},
  "fixed":{"name":"wtenv-research-e4-shared"},
  "pgdata":{"name":"wtenv-research-e4_pgdata"}}}
## E4.2 the image's own VOLUME (not in the compose model)
{"/var/lib/postgresql/data":{}} PGDATA=/var/lib/postgresql/data
## E4.3 runtime: db_none gets an anonymous, unlabelled volume from the image
volume name-length=64 dest=/var/lib/postgresql/data
$ docker volume ls --filter label=com.docker.compose.project=wtenv-research-e4 --format '{{.Name}}'
wtenv-research-e4-shared        # 2.24.4 only: `compose run` created every declared volume;
wtenv-research-e4_device        # the anonymous 64-character volume is not among the labelled ones
wtenv-research-e4_pgdata
```

Differences between the builds: on 2.24.4, `compose run` of one service created every declared
volume of the project; 5.1.4 created none. The anonymous volume is unlabelled on both. The
harness removes every one of them.

---

## R5. The variable-name rule for published ports (FR-011, FR-012)

**Question.** Which documented rule turns a published port into a variable name, so that the
name depends only on the service name, the container port and the protocol, and two published
ports never share one?

**Decision.**

```text
WTENV_PORT_<SERVICE>_<CONTAINER PORT>_<PROTOCOL>
```

- `<SERVICE>`: the compose service name in upper case, with **every** character other than
  `A–Z` and `0–9` replaced by `_` (one `_` per character; nothing is trimmed or collapsed).
- `<CONTAINER PORT>`: the container port (`target`) in decimal.
- `<PROTOCOL>`: `TCP` or `UDP`.

The result always matches `[A-Za-z_][A-Za-z0-9_]*`, because it starts with `WTENV_PORT_`.
The rule is a pure function of the three inputs. It does not depend on the block, the host
port, the worktree, the host IP or the order of services (FR-011).

**Collisions (FR-012).** Before anything changes, `up` builds the name of every published port
of the combined model and refuses with the new error code `port_variable_conflict` (exit 22),
naming both sources, when:

- two published ports get the same name. This covers service names that differ only in case or
  in which of `-`, `.` and `_` they use (`my-api` and `my_api`), and one container port
  published twice with the same protocol (on two host ports or two host addresses; E1.6b shows
  how a repository override produces this);
- a name equals a variable the configuration defines: a `ports` entry of `wtenv.toml`, or
  `DATABASE_URL` when a database is configured.

**Rationale.**

- The `WTENV_PORT_` prefix makes every name a valid variable name even for a service whose name
  starts with a digit (`2fa`), and keeps the names apart from the application's own variables.
  `exec` sets these variables, and the environment beats `.env` in Compose interpolation (E3.3).
  A prefix-free name such as `BACKEND_PORT` could silently change what the repository's compose
  file interpolates. `WTENV_PORT_…` cannot, unless the repository uses that exact name.
- Replacing each character one for one, with no trimming or collapsing, keeps the rule easy to
  state and makes `a-b` and `a--b` different names. The remaining collisions are names that
  differ only in case or punctuation. The spec allows refusing them ("either gives them
  different names or refuses").
- The protocol is always part of the name, so TCP and UDP on the same container port are
  different variables (`coredns`).
- The host IP is not in the name (the spec allows only the three inputs). One container port
  published on two host addresses therefore collides and is refused, as the spec's edge case
  says.

**Worked examples** (`research/out-e5.txt`; the fastapi rows come from the template's real
resolved model, `compose.yml` + `compose.override.yml`, every profile, on Compose 2.24.4):

| # | Service | Container port / protocol | Variable |
|---|---------|---------------------------|----------|
| 1 | `adminer` (fastapi) | 8080/tcp | `WTENV_PORT_ADMINER_8080_TCP` |
| 2 | `backend` (fastapi) | 8000/tcp | `WTENV_PORT_BACKEND_8000_TCP` |
| 3 | `db` (fastapi) | 5432/tcp | `WTENV_PORT_DB_5432_TCP` |
| 4 | `mailpit` (fastapi) | 8025/tcp | `WTENV_PORT_MAILPIT_8025_TCP` |
| 5 | `mailpit` (fastapi) | 1025/tcp | `WTENV_PORT_MAILPIT_1025_TCP` |
| 6 | `playwright` (fastapi) | 9323/tcp | `WTENV_PORT_PLAYWRIGHT_9323_TCP` |
| 7 | `proxy` (fastapi) | 80/tcp | `WTENV_PORT_PROXY_80_TCP` |
| 8 | `proxy` (fastapi) | 8080/tcp (host 8090 in the template) | `WTENV_PORT_PROXY_8080_TCP` |
| 9 | `coredns` | 53/udp and 53/tcp | `WTENV_PORT_COREDNS_53_UDP`, `WTENV_PORT_COREDNS_53_TCP` |
| 10 | `api.v2` | 8000/tcp | `WTENV_PORT_API_V2_8000_TCP` |
| 11 | `2fa` | 9000/tcp | `WTENV_PORT_2FA_9000_TCP` |
| 12 | `a-b` and `a--b` | 1/tcp | `WTENV_PORT_A_B_1_TCP` and `WTENV_PORT_A__B_1_TCP` (distinct) |
| 13 | `my-api` and `my_api` | 8000/tcp | both `WTENV_PORT_MY_API_8000_TCP` → `port_variable_conflict` |
| 14 | `API` and `api` | 80/tcp | both `WTENV_PORT_API_80_TCP` → `port_variable_conflict` |
| 15 | `web` twice (E1.6b) | 6379/tcp | both `WTENV_PORT_WEB_6379_TCP` → `port_variable_conflict` |

The template's eight published ports give eight distinct names. `adminer:8080` and
`proxy:8080` share a container port but not a name, because the service is part of it.

**Alternatives considered.**

- *`<SERVICE>_PORT_<PORT>` without a prefix* (close to the legacy Docker-links convention):
  invalid for a service name that starts with a digit, and the names can clash with the
  application's own variables.
- *An escaping scheme that never collides* (`_` written as `__`, and so on): unreadable for
  the common case, to cover a case the spec allows refusing.
- *Including the host IP*: not allowed by FR-011, which names exactly three inputs.

---

## R6. Smaller decisions

**Where the isolation file lives.** `.wtenv/compose.isolation.yaml` in the worktree root, for
every worktree whose repository owns its override. The directory is the one SQLite copies
already use (`.wtenv/`, 001 files.md). The name ends in `.isolation.yaml`, not in
`.override.yaml`, so Compose never loads it automatically, whatever directory the compose file
is in (E1, 001 research §4: Compose loads only the four `*.override.y*ml` names). It is
recorded in the existing `ComposeRecord.override` field, so every 001 guard that protects the
generated override (form, symbolic links, header, `removing` state, exclude entry) applies to
it unchanged. The only guard change is that `compose.override_problem` accepts this one exact
path as a name wtenv records (plan.md, Safety).

**Registry: no migration (FR-029).** One optional field is added, `ComposeRecord.repository_override`
(the path of the repository's override, relative to the worktree root). It is serialised **only
when set** (pydantic `Field(exclude_if=...)`, available in the pinned pydantic 2.13), so a worktree
that does not use the new mode is saved byte for byte as v0.1.1 saves it, and v0.1.x can still
read the registry. `REGISTRY_VERSION` stays 1. A registry written by v0.1.x parses unchanged,
because the field defaults to None. The compose-hosted database and the port variables need no
recorded fact: the database creates nothing wtenv must remove (FR-022, FR-023), and the
variable names are derived from what the registry already records (`PublishedPort.service`,
`target`, `protocol`). No migration is needed, so the spec does not have to be revisited.

**No new dependencies.** Everything above uses the standard library, the existing `docker
compose` subprocess, and pydantic features already pinned. Principle VIII holds.

**Startup (NFR-001).** The checks `exec` adds read the registry entry it already reads, and run
`lstat`/`open` on at most five paths. They live in a new standard-library-only module
(`wtenv/isolation.py`) so that `exec` still does not import `wtenv.compose`.
