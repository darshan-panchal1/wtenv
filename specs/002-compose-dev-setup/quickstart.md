# Quickstart: acceptance run on the fastapi template (SC-001 to SC-005)

**Feature**: `002-compose-dev-setup` | **Date**: 2026-10-10

This is the validation guide for the finished feature. It runs the dev flow of
`fastapi/full-stack-fastapi-template` in three worktrees at the same time, using the
repository's own `compose.override.yml`, and checks SC-001 to SC-005. It does not describe the
implementation: see [plan.md](plan.md), [data-model.md](data-model.md) and the
[contracts](contracts/).

Expected values below are the ones the contracts define. Port numbers depend on which blocks are
free; the checks compare structure, not exact numbers.

## Prerequisites

- wtenv built from this branch on `PATH` (`uv tool install --force .` from the repository root).
- Docker with Compose **2.24.4 or later**, local engine. Run the whole guide once with Compose
  2.24.4 (the CI minimum) and once with the newest Compose.
- `git` 2.31 or later, `jq`, `curl`, `uv`.
- About 3 GB free for the template's images (`postgres:18`, `adminer`, `axllent/mailpit`, and the
  `backend` image, which all three worktrees build under the template's fixed tag
  `backend:latest`; spec, Assumptions).
- Ports 80, 1025, 5432, 8000, 8025, 8080, 8090 may be in use or free: wtenv never uses them.

## 0. Baseline

```sh
export ACC=$(mktemp -d)                      # everything this guide creates lives here
docker ps -a --format '{{.ID}} {{.Names}}' | sort > "$ACC/ps.before"
docker volume ls --format '{{.Name}}'      | sort > "$ACC/volumes.before"
docker network ls --format '{{.Name}}'     | sort > "$ACC/networks.before"
```

## 1. The repository, with `wtenv.toml` committed on a local branch

No tracked file is edited; one file is added (Principle V: compose and database features are
enabled only through a committed `wtenv.toml`).

```sh
git clone https://github.com/fastapi/full-stack-fastapi-template "$ACC/fastapi"
cd "$ACC/fastapi"
git switch -c wtenv-acceptance
cat > wtenv.toml <<'EOF'
ports = ["PORT"]

[compose]
file = "compose.yml"

[database]
type = "compose"
service = "db"
url = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/app"
EOF
git add wtenv.toml && git commit -q -m "Add wtenv.toml for the acceptance run"
export OVERRIDE_BLOB=$(git rev-parse HEAD:compose.override.yml)   # the committed content hash
for n in 1 2 3; do git worktree add -q "$ACC/wt$n" -b "wt$n"; done
```

## 2. Provision the three worktrees (US1, US2, US3)

```sh
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv up --json > "$ACC/up$n.json"); echo "wt$n exit $?"; done
```

Expected for each worktree ([contracts/cli.md](contracts/cli.md), `wtenv up`):

```sh
jq '{ok, isolation: .worktree.compose_isolation, repo: .worktree.compose_repository_override,
     db: .compose_database, warnings: [.warnings[].code]}' "$ACC/up1.json"
```

- `ok` true, exit 0.
- `isolation` `"exec_only"`, `repo` `"compose.override.yml"` (FR-001, FR-005).
- `db`: `service` `"db"`, `target` 5432, `port_variable` `"WTENV_PORT_DB_5432_TCP"` (FR-019).
- `warnings` contains `compose_isolation_exec_only` (FR-004) and no `compose_database_data_kept`
  (the template stores its data in the project volume `app-db-data`).
- `.worktree.ports` has 9 entries: `PORT` and the 8 published ports, each published one with its
  `port_variable` (FR-014).
- The repository's override is unchanged, and `git status` is clean (US1 scenario 1):

```sh
for n in 1 2 3; do
  (cd "$ACC/wt$n" && test "$(git hash-object compose.override.yml)" = "$OVERRIDE_BLOB" \
     && test -z "$(git status --porcelain)" && echo "wt$n: override intact, status clean")
done
```

- The env file carries the variables, with the same names in every worktree and different values
  (US2 scenario 3, SC-002):

```sh
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv exec -- env | grep -E '^(WTENV_PORT_|DATABASE_URL=)' | sed 's/=.*//' | sort | sha256sum); done   # three identical lines
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv exec -- sh -c 'echo $WTENV_PORT_BACKEND_8000_TCP'); done                                       # three different ports
```

- A plain `docker compose` is **not** isolated, as documented (FR-004):

```sh
(cd "$ACC/wt1" && docker compose config --format json | jq -r '.name, .services.backend.ports[0].published')   # "wt1", "8000"
(cd "$ACC/wt1" && wtenv exec -- docker compose config --format json | jq -r '.name, .services.backend.ports[0].published')   # "wtenv-wt1-<id8>", a block port
```

## 3. Run the dev flow in all three at the same time (SC-001)

The template's documented Compose flow is `docker compose run --rm backend bash scripts/prestart.sh`
then `docker compose watch`. Traefik (`proxy`) and Playwright are not started (SC-001).

```sh
for n in 1 2 3; do
  (cd "$ACC/wt$n" \
   && wtenv exec -- docker compose run --rm backend bash scripts/prestart.sh \
   && wtenv exec -- docker compose up -d --build --wait backend db adminer mailpit)
done
# Optional, to exercise `watch` (a foreground client): one per worktree, stopped at step 7.
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv exec -- docker compose watch --no-up backend > "$ACC/watch$n.log" 2>&1 &); done
```

Checks while the stacks run:

```sh
for n in 1 2 3; do jq -r '.worktree.compose_project' "$ACC/up$n.json"; done > "$ACC/projects"
# All 15 published host ports (5 per worktree), distinct, each inside its worktree's block:
for n in 1 2 3; do
  P=$(sed -n "${n}p" "$ACC/projects"); S=$(jq .worktree.block.start "$ACC/up$n.json"); E=$(jq .worktree.block.end "$ACC/up$n.json")
  docker ps --filter "label=com.docker.compose.project=$P" --format '{{.Ports}}' \
    | grep -oE '0\.0\.0\.0:[0-9]+' | cut -d: -f2 | sort -un \
    | awk -v s="$S" -v e="$E" -v w="wt$n" '{ if ($1 < s || $1 > e) print "OUT OF BLOCK", w, $1; else print $1 }'
done | tee "$ACC/ports"
test "$(grep -c . "$ACC/ports")" -eq 15 && test "$(sort -u "$ACC/ports" | grep -c .)" -eq 15 && ! grep -q OUT "$ACC/ports" && echo "SC-001 ports: 15 distinct, all in block"
# Three distinct database volumes, each the project's own:
while read -r P; do docker volume ls --filter "label=com.docker.compose.project=$P" --format '{{.Name}}' | grep -x "${P}_app-db-data"; done < "$ACC/projects"
# The repository's override is unchanged while the stacks run:
for n in 1 2 3; do (cd "$ACC/wt$n" && test "$(git hash-object compose.override.yml)" = "$OVERRIDE_BLOB" && echo "wt$n: override intact"); done
```

Expected: 15 distinct ports, all in their blocks; three lines `wtenv-wt<n>-<id8>_app-db-data`;
three "override intact".

## 4. Each agent finds its own backend (SC-002)

```sh
for n in 1 2 3; do
  (cd "$ACC/wt$n" && wtenv exec -- sh -c 'curl -fsS "http://localhost:$WTENV_PORT_BACKEND_8000_TCP/api/v1/utils/health-check/"' && echo " wt$n ok")
done
```

To prove the answer comes from **that** worktree's backend, stop wt2's backend and repeat: wt1
and wt3 still answer, wt2 fails to connect.

```sh
(cd "$ACC/wt2" && wtenv exec -- docker compose stop backend)
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv exec -- sh -c 'curl -fsS -m 3 "http://localhost:$WTENV_PORT_BACKEND_8000_TCP/api/v1/utils/health-check/"' >/dev/null && echo "wt$n answers" || echo "wt$n refused"); done
(cd "$ACC/wt2" && wtenv exec -- docker compose start backend)
```

## 5. Each worktree writes to its own database (SC-003)

A host-side command, through `wtenv exec`, so `DATABASE_URL` is the worktree's and not the
tracked `.env`'s `localhost:5432` (US3 scenario 3).

```sh
for n in 1 2 3; do
  (cd "$ACC/wt$n" && wtenv exec -- uv run --no-project --with 'psycopg[binary]' python -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    c.execute('CREATE TABLE IF NOT EXISTS wtenv_acceptance (worktree text)')
    c.execute('INSERT INTO wtenv_acceptance VALUES (%s)', ('wt$n',))
")
done
for n in 1 2 3; do
  (cd "$ACC/wt$n" && wtenv exec -- uv run --no-project --with 'psycopg[binary]' python -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c:
    print('wt$n sees', [r[0] for r in c.execute('SELECT worktree FROM wtenv_acceptance ORDER BY 1')])
")
done
```

Expected: `wt1 sees ['wt1']`, `wt2 sees ['wt2']`, `wt3 sees ['wt3']`. The tracked `.env` is
unchanged (`git status` clean).

## 6. Repeat `up` is identical (SC-005) and `exec` fails closed (FR-033)

```sh
cd "$ACC/wt1"
for i in $(seq 10); do wtenv up --json | jq -c '[.worktree.ports, .worktree.compose_project]'; sha256sum .env.local .wtenv/compose.isolation.yaml | cut -c1-16; done | sort -u | wc -l   # 3: one JSON line, two hashes
COMPOSE_PROJECT_NAME=x wtenv exec --json -- touch "$ACC/must-not-exist"; echo "exit $?"           # exit 125
test ! -e "$ACC/must-not-exist" && echo "command not run"
jq -n 'input.error | {code, reason: .details.reason}' <(COMPOSE_PROJECT_NAME=x wtenv exec --json -- true)   # isolation_unavailable, compose_env_override
```

Repeat the 10-run loop in a scratch repository without an override of its own to cover "with and
without a repository-owned override" (SC-005). There, `compose_isolation` is `"automatic"` and
the override file is byte-identical to v0.1.1's (FR-008, SC-006).

## 7. Tear down (SC-004)

```sh
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv down --dry-run --json > "$ACC/dry$n.json"); done
jq '[.would_remove[] | select(.kind == "compose_volume") | .name]' "$ACC/dry1.json"   # ["wtenv-wt1-<id8>_app-db-data"]
jq '[.would_remove[], .kept_items[] | .name] | map(select(endswith("compose.override.yml")))' "$ACC/dry1.json"   # []  (FR-002)
pkill -f 'docker compose watch --no-up backend' || true
for n in 1 2 3; do (cd "$ACC/wt$n" && wtenv down --json > "$ACC/down$n.json"); echo "wt$n exit $?"; done
while read -r P; do for kind in "ps -a" "network ls" "volume ls"; do docker $kind --filter "label=com.docker.compose.project=$P" -q; done; done < "$ACC/projects" | wc -l   # 0
for n in 1 2 3; do (cd "$ACC/wt$n" && test "$(git hash-object compose.override.yml)" = "$OVERRIDE_BLOB" && test ! -e .wtenv/compose.isolation.yaml && echo "wt$n: override intact, isolation file gone"); done
```

Expected: each `down` exits 0; no container, network or volume of the three projects remains;
the repository's override is byte-identical in all three; wtenv's isolation file is gone.

Everything outside the three projects is unchanged. The only differences allowed are the
template's images, the `backend:latest` tag, and anonymous volumes listed in `kept_volumes`
(none for this template):

```sh
docker ps -a --format '{{.ID}} {{.Names}}' | sort | diff "$ACC/ps.before" -
docker volume ls --format '{{.Name}}'      | sort | diff "$ACC/volumes.before" -
docker network ls --format '{{.Name}}'     | sort | diff "$ACC/networks.before" -
```

## 8. Clean up the scratch repository

```sh
cd "$ACC/fastapi" && for n in 1 2 3; do git worktree remove "$ACC/wt$n"; done
rm -rf "$ACC"
```

## What each step proves

| Step | Proves |
|------|--------|
| 2 | FR-001, FR-002, FR-005, FR-010, FR-011, FR-014, FR-019; US1 scenarios 1, 9; US2 scenarios 1, 3, 7; US3 scenario 1 |
| 3 | SC-001; FR-003; US1 scenarios 2, 3, 8 |
| 4 | SC-002 |
| 5 | SC-003; US3 scenarios 2, 3 |
| 6 | SC-005; FR-033; US1 scenarios 4, 10; US2 scenario 4 |
| 7 | SC-004; FR-002, FR-023; US1 scenario 5; US3 scenario 5 |
