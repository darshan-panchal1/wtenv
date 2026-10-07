# Quickstart and end-to-end acceptance test

**Feature**: `001-worktree-runtime-isolation` | **Date**: 2026-10-03 | **Plan**: [plan.md](plan.md)

This page is both a walkthrough and the end-to-end acceptance test for v1. Every `bash`
block below is one part of a single script. Run them all, in order, with:

```text
awk '/^```bash$/{f=1;next} /^```$/{f=0} f' \
  specs/001-worktree-runtime-isolation/quickstart.md > /tmp/wtenv-acceptance.sh
bash /tmp/wtenv-acceptance.sh
```

The script stops at the first failed check with `FAIL: …`, and prints `ok - …` for each
check that passes. It covers every user story and the success criteria SC-001 to SC-009.
Exit-status numbers come from [contracts/cli.md](contracts/cli.md).

## Prerequisites

| Need | Why |
|------|-----|
| macOS or Linux (WSL2 counts) | NFR-004 |
| git 2.31 or later | research.md, section 6 |
| Docker running, with Compose 2.24.4 or later | Postgres server and compose stacks |
| `uv`, `python3`, `curl` | Sample app environment, JSON checks, health checks |
| The wtenv build under test on `PATH` | Install it with `uv tool install --force --reinstall .` from the checkout |
| The sample app fixture at `tests/fixtures/sample_app/` | Created by the tasks; contents below |

Nothing here touches the developer's own registry: the script points `XDG_STATE_HOME` at a
temporary directory, so wtenv's state lives there (contracts/files.md, "State directory").
The script removes everything it created when it exits; set `QS_KEEP=1` to keep it for
debugging.

### The sample app fixture

`tests/fixtures/sample_app/` holds the acceptance fixture the spec describes (Assumptions,
"Sample app"):

- `app.py`: a FastAPI app with `GET /health`, returning
  `{"port": <PORT>, "database": <current_database()>, "items": <row count of items>}`. It reads
  `PORT` and `DATABASE_URL` from the environment.
- `compose.yaml`: one service `cache` using `redis:7-alpine`, publishing
  `"${CACHE_PORT:-6379}:6379"`, with a named volume `cachedata`.
- `wtenv.toml`:

  ```toml
  ports = ["PORT", "CACHE_PORT"]
  block_size = 10

  [database]
  type = "postgres"
  template = "app_template"
  url = "postgresql://postgres:{env:QS_PG_PASSWORD}@localhost:15432/{name}"

  [compose]
  file = "compose.yaml"
  ```

---

## 0. Set up

```bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel)}"
FIXTURE="$REPO_ROOT/tests/fixtures/sample_app"
WORK="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/wtenv-acceptance.XXXXXX")" && pwd -P)"
export XDG_STATE_HOME="$WORK/state"
export GIT_AUTHOR_NAME=qs GIT_AUTHOR_EMAIL=qs@example.invalid
export GIT_COMMITTER_NAME=qs GIT_COMMITTER_EMAIL=qs@example.invalid
export QS_PG_PASSWORD=quickstart PGPASSWORD=quickstart
PG=wtenv-acceptance-pg
SERVER_PIDS=()

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok - $*"; }

# expect_exit STATUS COMMAND...: runs COMMAND, keeps its stdout in $OUT, checks the status.
expect_exit() {
  local want=$1; shift
  set +e; OUT=$("$@"); local got=$?; set -e
  [ "$got" -eq "$want" ] || fail "expected exit $want, got $got: $*"
}

# jget EXPR: evaluates a Python expression over the JSON document on stdin, called d.
jget() { python3 -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {"d": d}))' "$1"; }

psql_q() { docker exec -i "$PG" psql -U postgres -X -At -v ON_ERROR_STOP=1 "$@"; }

cleanup() {
  local rc=$?
  for pid in "${SERVER_PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done
  if [ "${QS_KEEP:-0}" = "1" ]; then
    echo "kept: $WORK (Postgres container $PG still running)"
  else
    # Remove only compose projects whose working directory is under $WORK.
    for id in $(docker ps -aq --filter "label=com.docker.compose.project"); do
      dir=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id")
      project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$id")
      case "$dir" in
        "$WORK"/*) docker compose -p "$project" down --volumes --remove-orphans >/dev/null 2>&1 || true ;;
      esac
    done
    docker rm -f -v "$PG" >/dev/null 2>&1 || true
    rm -rf "$WORK"
  fi
  exit "$rc"
}
trap cleanup EXIT

command -v wtenv >/dev/null || fail "wtenv is not on PATH"
docker info >/dev/null 2>&1 || fail "Docker is not running"
[ -d "$FIXTURE" ] || fail "missing fixture $FIXTURE"
ok "prerequisites ($(wtenv --version))"

# One shared local Postgres server with a template database (spec Assumptions).
docker rm -f -v "$PG" >/dev/null 2>&1 || true
docker run -d --name "$PG" -e POSTGRES_PASSWORD="$QS_PG_PASSWORD" \
  -p 127.0.0.1:15432:5432 postgres:17 >/dev/null
for _ in $(seq 1 60); do docker exec "$PG" pg_isready -U postgres -q 2>/dev/null && break; sleep 0.5; done
sleep 1
psql_q -d postgres -c "CREATE DATABASE app_template"
psql_q -d app_template -c "CREATE TABLE items (id int PRIMARY KEY); INSERT INTO items SELECT generate_series(1, 3)"
ok "shared Postgres server on 127.0.0.1:15432 with template app_template"

# One Python environment for the sample app, shared by every worktree.
uv venv -q "$WORK/venv"
uv pip install -q --python "$WORK/venv/bin/python" fastapi uvicorn "psycopg[binary]"
ok "sample app environment"
```

## 1. Ports and env file with no configuration (User Story 1, SC-006, SC-009)

```bash
git init -q -b main "$WORK/plain"
git -C "$WORK/plain" commit -q --allow-empty -m init
git -C "$WORK/plain" worktree add -q "$WORK/plain-b" -b b

(cd "$WORK/plain" && wtenv up >/dev/null)
(cd "$WORK/plain-b" && wtenv up >/dev/null)
PORT_A=$(sed -n 's/^PORT=//p' "$WORK/plain/.env.local")
PORT_B=$(sed -n 's/^PORT=//p' "$WORK/plain-b/.env.local")
[ -n "$PORT_A" ] && [ -n "$PORT_B" ] && [ "$PORT_A" != "$PORT_B" ] || fail "ports $PORT_A / $PORT_B"
ok "two worktrees, two blocks, PORT=$PORT_A and PORT=$PORT_B (US1 scenarios 1-2, SC-009)"

python3 - "$WORK/plain/.env.local" <<'PY' || fail ".env.local is not mode 0600"
import os, stat, sys
assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o600
PY
ok "env file created with mode 0600 (FR-019)"

[ -z "$(git -C "$WORK/plain" status --porcelain)" ] || fail "generated files show in git status"
[ -z "$(git -C "$WORK/plain-b" status --porcelain)" ] || fail "generated files show in git status (linked)"
ok "git status is clean in both worktrees (FR-018)"

cp "$WORK/plain-b/.env.local" "$WORK/env-before"
(cd "$WORK/plain-b/" && mkdir -p sub && cd sub && wtenv up --json) > "$WORK/up-again.json"
cmp -s "$WORK/env-before" "$WORK/plain-b/.env.local" || fail "env file changed on repeat up"
[ "$(jget 'd["worktree"]["block"]["start"]' < "$WORK/up-again.json")" = "$PORT_B" ] || fail "block moved"
ok "repeat up from a subdirectory: same block, byte-identical env file (US1 scenario 3, SC-006)"

printf 'SECRET=keep-me\nPORT=1\n' > "$WORK/plain-c.env"
git -C "$WORK/plain" worktree add -q "$WORK/plain-c" -b c
cp "$WORK/plain-c.env" "$WORK/plain-c/.env.local"
(cd "$WORK/plain-c" && wtenv up 2> "$WORK/up-c.err" >/dev/null)
head -2 "$WORK/plain-c/.env.local" | cmp -s - "$WORK/plain-c.env" || fail "developer lines changed"
grep -q '^# >>> wtenv managed' "$WORK/plain-c/.env.local" || fail "no marked section"
grep -q 'env_duplicate_variable' "$WORK/up-c.err" || fail "no duplicate warning"
ok "developer lines kept byte for byte, section appended, duplicate PORT warned (US1 scenario 6)"
```

## 2. Simultaneous provisioning (FR-013, SC-005)

```bash
for i in 1 2 3 4 5; do git -C "$WORK/plain" worktree add -q "$WORK/par-$i" -b "par-$i"; done
for trial in $(seq 1 20); do
  for i in 1 2 3 4 5; do (cd "$WORK/par-$i" && wtenv up >/dev/null) & done
  wait
  PORTS=$(for i in 1 2 3 4 5; do sed -n 's/^PORT=//p' "$WORK/par-$i/.env.local"; done | sort -u | wc -l | tr -d ' ')
  [ "$PORTS" -eq 5 ] || fail "trial $trial: only $PORTS distinct ports"
  for i in 1 2 3 4 5; do (cd "$WORK/par-$i" && wtenv down >/dev/null); done
done
ok "5 simultaneous up runs, 20 of 20 trials without a shared port (SC-005)"
```

## 3. Databases (User Story 2)

```bash
mkdir -p "$WORK/app"
cp -R "$FIXTURE/." "$WORK/app/"
git -C "$WORK/app" init -q -b main
git -C "$WORK/app" add -A
git -C "$WORK/app" commit -q -m "sample app"
git -C "$WORK/app" worktree add -q "$WORK/app-a" -b a
git -C "$WORK/app" worktree add -q "$WORK/app-b" -b b

(cd "$WORK/app-a" && wtenv up --json) > "$WORK/up-a.json"
(cd "$WORK/app-b" && wtenv up --json) > "$WORK/up-b.json"
DB_A=$(jget 'd["worktree"]["databases"][0]["name"]' < "$WORK/up-a.json")
DB_B=$(jget 'd["worktree"]["databases"][0]["name"]' < "$WORK/up-b.json")
[ "$DB_A" != "$DB_B" ] || fail "same database"
grep -q "/$DB_A'*\$" "$WORK/app-a/.env.local" || fail "DATABASE_URL does not name $DB_A"
! grep -q "$QS_PG_PASSWORD" "$WORK/up-a.json" || fail "password printed in JSON output"
ok "each worktree has its own copy of the template; no credentials in output (US2 scenario 1, FR-019)"

psql_q -d "$DB_A" -c "ALTER TABLE items ADD COLUMN note text; INSERT INTO items VALUES (4, 'a')"
[ "$(psql_q -d "$DB_B" -c 'SELECT count(*) FROM items')" = 3 ] || fail "worktree b changed"
[ "$(psql_q -d app_template -c 'SELECT count(*) FROM items')" = 3 ] || fail "template changed"
ok "schema change in a leaves b and the template alone (US2 scenario 2)"

(cd "$WORK/app-a" && wtenv up >/dev/null)
[ "$(psql_q -d "$DB_A" -c 'SELECT count(*) FROM items')" = 4 ] || fail "data lost on repeat up"
ok "repeat up keeps the database and its data (US2 scenario 3)"

docker exec -d "$PG" psql -U postgres -d app_template -c "SELECT pg_sleep(30)"
sleep 1
git -C "$WORK/app" worktree add -q "$WORK/app-c" -b c
START=$(date +%s)
expect_exit 10 sh -c "cd '$WORK/app-c' && wtenv up --json"
[ $(( $(date +%s) - START )) -lt 4 ] || fail "template_in_use was not reported at once"
[ "$(printf '%s' "$OUT" | jget 'd["error"]["code"]')" = template_in_use ] || fail "wrong code"
[ "$(printf '%s' "$OUT" | jget 'd["error"]["details"]["connections"]')" = 1 ] || fail "wrong count"
[ "$(psql_q -d postgres -c "SELECT count(*) FROM pg_stat_activity WHERE datname = 'app_template'")" = 1 ] \
  || fail "the other session was closed"
(cd "$WORK/app-c" && wtenv ls --json) | jget '[w["status"] for w in d["worktrees"] if w["path"].endswith("/app-c")][0]' \
  | grep -qx incomplete || fail "app-c is not incomplete"
psql_q -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'app_template'" >/dev/null
(cd "$WORK/app-c" && wtenv up >/dev/null)
ok "busy template: exit 10 at once, session untouched, worktree incomplete, later up completes (US2 scenario 8)"
```

## 4. Compose stacks (User Story 3)

```bash
(cd "$WORK/app-a" && docker compose up -d --quiet-pull >/dev/null 2>&1)
(cd "$WORK/app-b" && docker compose up -d --quiet-pull >/dev/null 2>&1)
PROJ_A=$(jget 'd["worktree"]["compose_project"]' < "$WORK/up-a.json")
PROJ_B=$(jget 'd["worktree"]["compose_project"]' < "$WORK/up-b.json")
[ "$PROJ_A" != "$PROJ_B" ] || fail "same project"
CACHE_A=$(sed -n 's/^CACHE_PORT=//p' "$WORK/app-a/.env.local")
docker ps --filter "label=com.docker.compose.project=$PROJ_A" --format '{{.Ports}}' | grep -q ":$CACHE_A->6379" \
  || fail "cache of a is not on CACHE_PORT=$CACHE_A"
ok "plain docker compose up in two worktrees: two projects, cache of a on its CACHE_PORT (US3 scenarios 1-3)"

cp "$WORK/app-a/compose.override.yaml" "$WORK/override-before"
(cd "$WORK/app-a" && wtenv up >/dev/null)
cmp -s "$WORK/override-before" "$WORK/app-a/compose.override.yaml" || fail "override changed"
[ -z "$(git -C "$WORK/app-a" status --porcelain)" ] || fail "compose.yaml modified or override visible"
ok "override byte-identical on repeat up; committed compose file untouched (US3 scenarios 4-5)"
```

## 5. Five worktrees at once (SC-001)

```bash
for i in 1 2 3; do
  git -C "$WORK/app" worktree add -q "$WORK/app-s$i" -b "s$i"
  (cd "$WORK/app-s$i" && wtenv up >/dev/null)
done
SEEN=""
for wt in app-a app-b app-s1 app-s2 app-s3; do
  (cd "$WORK/$wt" && docker compose up -d --quiet-pull >/dev/null 2>&1)
  (cd "$WORK/$wt" && exec wtenv exec -- sh -c "exec '$WORK/venv/bin/uvicorn' app:app --port \"\$PORT\" --log-level warning") &
  SERVER_PIDS+=($!)
done
for wt in app-a app-b app-s1 app-s2 app-s3; do
  port=$(sed -n 's/^PORT=//p' "$WORK/$wt/.env.local")
  for _ in $(seq 1 40); do curl -fsS "http://127.0.0.1:$port/health" -o "$WORK/health-$wt.json" 2>/dev/null && break; sleep 0.25; done
  [ -s "$WORK/health-$wt.json" ] || fail "no answer from the API server of $wt on port $port"
  db=$(jget 'd["database"]' < "$WORK/health-$wt.json")
  case " $SEEN " in *" $db "*) fail "$wt shares database $db";; esac
  SEEN="$SEEN $db"
  [ -n "$(cd "$WORK/$wt" && docker compose ps -q)" ] || fail "compose stack of $wt is not running"
done
ok "5 API servers, 5 databases, 5 compose stacks running at once, no file edited by hand (SC-001)"
for pid in "${SERVER_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
SERVER_PIDS=()
```

## 6. Teardown and garbage collection (User Story 4, SC-003, SC-007)

```bash
# Decoys with wtenv-style names that wtenv did not create (SC-007).
psql_q -d postgres -c "CREATE DATABASE wtenv_decoy_00000000"
docker volume create --label com.docker.compose.project=wtenv-decoy-00000000 wtenv-decoy-vol >/dev/null

(cd "$WORK/app-s1" && wtenv down --dry-run --json) > "$WORK/dry.json"
jget 'sorted({i["kind"] for i in d["would_remove"]})' < "$WORK/dry.json" | grep -q postgres_database || fail "dry run incomplete"
psql_q -d postgres -c "SELECT 1 FROM pg_database WHERE datname LIKE 'wtenv_app_s1_%'" | grep -qx 1 || fail "dry run removed something"
ok "down --dry-run lists the database and changes nothing (US4 scenario 2)"

(cd "$WORK/app-s1" && wtenv down >/dev/null)
git -C "$WORK/app" worktree remove --force "$WORK/app-s2"
git -C "$WORK/app" worktree remove --force "$WORK/app-s3"
(wtenv gc --dry-run --json) > "$WORK/gc-dry.json"
[ "$(jget 'len(d["would_release"])' < "$WORK/gc-dry.json")" = 2 ] || fail "gc dry run should release 2"
wtenv gc --json > "$WORK/gc.json"
[ "$(jget 'len(d["released"])' < "$WORK/gc.json")" = 2 ] || fail "gc should release 2"
for name in s1 s2 s3; do
  [ -z "$(psql_q -d postgres -c "SELECT datname FROM pg_database WHERE datname LIKE 'wtenv_app_${name}_%'")" ] || fail "database of $name left"
  [ -z "$(docker ps -aq --filter "label=com.docker.compose.project.working_dir=$WORK/app-$name")" ] || fail "container of $name left"
done
[ "$(wtenv ls --json | jget 'len([w for w in d["worktrees"] if w["status"] == "orphaned"])')" = 0 ] || fail "orphaned entries left"
ok "down in one, git worktree remove in two, one gc: no database, container, or entry left (US4 scenarios 1, 3-4, SC-003)"

psql_q -d postgres -c "SELECT 1 FROM pg_database WHERE datname = 'wtenv_decoy_00000000'" | grep -qx 1 || fail "decoy database removed"
docker volume inspect wtenv-decoy-vol >/dev/null 2>&1 || fail "decoy volume removed"
ok "decoys with wtenv-style names untouched (SC-007)"

# A directory deleted by hand while git still lists it is kept until git prune (FR-045).
git -C "$WORK/app" worktree add -q "$WORK/app-gone" -b gone
(cd "$WORK/app-gone" && wtenv up >/dev/null)
rm -rf "$WORK/app-gone"
wtenv gc --json > "$WORK/gc-kept.json"
jget '[k["reason"] for k in d["kept"] if k["path"].endswith("/app-gone")][0]' < "$WORK/gc-kept.json" \
  | grep -qx git_still_lists || fail "hand-deleted worktree not kept"
git -C "$WORK/app" worktree prune
wtenv gc --json > "$WORK/gc-pruned.json"
jget 'any(p.endswith("/app-gone") for p in d["released"])' < "$WORK/gc-pruned.json" | grep -qx True \
  || fail "not released after prune"
ok "deleted by hand: kept as git_still_lists, released after git worktree prune (US4 scenario 8)"

# A deleted repository is kept until it is named with --release (FR-073).
git init -q -b main "$WORK/doomed"
git -C "$WORK/doomed" commit -q --allow-empty -m init
(cd "$WORK/doomed" && wtenv up >/dev/null)
rm -rf "$WORK/doomed"
wtenv gc --json | jget '[k["reason"] for k in d["kept"] if k["path"].endswith("/doomed")][0]' \
  | grep -qx repository_not_found || fail "deleted repository not kept"
expect_exit 18 sh -c "wtenv gc --release '$WORK/app-a' --json"
wtenv gc --release "$WORK/doomed" --json | jget 'len(d["released"])' | grep -qx 1 || fail "release failed"
wtenv gc --release "$WORK/doomed" --json | jget 'd["no_entry"]' | grep -q doomed || fail "release not repeatable"
ok "deleted repository kept; --release refuses a live worktree (exit 18), releases the named one, repeats safely (US4 scenario 9)"

wtenv ls --json > "$WORK/ls.json"
jget 'sorted({w["status"] for w in d["worktrees"]})' < "$WORK/ls.json" | grep -q provisioned || fail "ls"
(cd "$WORK/plain" && wtenv down >/dev/null && wtenv down >/dev/null)
ok "ls lists every worktree with its status; down twice is harmless (US4 scenarios 6-7)"
```

## 7. Auto-provisioning hook and exec (User Story 5)

```bash
printf '#!/bin/sh\necho "developer hook ran" >&2\n' > "$WORK/app/.git/hooks/post-checkout"
chmod +x "$WORK/app/.git/hooks/post-checkout"
(cd "$WORK/app" && wtenv hook install >/dev/null)
grep -q 'developer hook ran' "$WORK/app/.git/hooks/post-checkout" || fail "existing hook altered"

git -C "$WORK/app" worktree add -q "$WORK/app-hook" -b hook 2>/dev/null
(cd "$WORK/app-hook" && wtenv ls --json) | jget '[w["status"] for w in d["worktrees"] if w["path"].endswith("/app-hook")][0]' \
  | grep -qx provisioned || fail "hook did not provision"
ok "git worktree add provisions the new worktree; existing hook content kept (US5 scenarios 1, 7)"

docker exec -d "$PG" psql -U postgres -d app_template -c "SELECT pg_sleep(20)"
sleep 1
git -C "$WORK/app" worktree add -q "$WORK/app-hookfail" -b hookfail 2> "$WORK/hookfail.err" || fail "git failed"
[ -d "$WORK/app-hookfail" ] && grep -q 'template_in_use' "$WORK/hookfail.err" || fail "hook failure not reported"
psql_q -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'app_template'" >/dev/null
(cd "$WORK/app-hookfail" && wtenv up >/dev/null)
ok "provisioning failure in the hook: worktree still created, error on stderr, later up completes (US5 scenario 2)"

BEFORE=$(wtenv ls --json | jget 'len(d["worktrees"])')
git -C "$WORK/app-hook" switch -q -c hook-other
[ "$(wtenv ls --json | jget 'len(d["worktrees"])')" = "$BEFORE" ] || fail "switch triggered the hook"
ok "branch switch does nothing (US5 scenario 3)"

PORT_HOOK=$(sed -n 's/^PORT=//p' "$WORK/app-hook/.env.local")
[ "$(cd "$WORK/app-hook" && wtenv exec -- sh -c 'echo "$PORT"')" = "$PORT_HOOK" ] || fail "exec env"
expect_exit 7 sh -c "cd '$WORK/app-hook' && wtenv exec -- sh -c 'exit 7'"
git -C "$WORK/app" worktree add -q --no-checkout "$WORK/app-bare" -b bare
expect_exit 125 sh -c "cd '$WORK/app-bare' && wtenv exec --json -- true"
[ "$(printf '%s' "$OUT" | jget 'd["error"]["code"]')" = not_provisioned ] || fail "wrong exec error"
ok "exec passes variables and the exit status; refuses an unprovisioned worktree with 125 (US5 scenarios 5-6)"

(cd "$WORK/app" && wtenv hook uninstall --dry-run --json) | jget 'd["would_remove"]' | grep -q hook_block || fail "dry run"
(cd "$WORK/app" && wtenv hook uninstall >/dev/null)
printf '#!/bin/sh\necho "developer hook ran" >&2\n' | cmp -s - "$WORK/app/.git/hooks/post-checkout" || fail "uninstall left changes"
ok "uninstall removes exactly what install added (US5 scenario 7, FR-053)"
```

## 8. JSON contract and diagnostics (User Story 6, SC-008)

```bash
cd "$WORK/app-a"
for cmd in "--version --json" "ls --json" "doctor --json" "up --json" "gc --dry-run --json" "down --dry-run --json"; do
  set +e; wtenv $cmd > "$WORK/one.json"; set -e
  python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$WORK/one.json" || fail "wtenv $cmd: not one JSON document"
done
ok "every command prints exactly one JSON document with --json (US6 scenario 1)"

expect_exit 2 wtenv up --bogus --json
[ "$(printf '%s' "$OUT" | jget 'd["error"]["code"]')" = usage_error ] || fail "usage error code"
ok "a failing command carries a stable code in JSON (US6 scenario 2)"

expect_exit 0 wtenv doctor --json
ok "doctor on a healthy machine: exit 0 (US6 scenario 3)"

PORT_OF_B=$(sed -n 's/^PORT=//p' "$WORK/app-b/.env.local")
python3 -c 'import socket, sys, time; s = socket.socket(); s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(30)' "$PORT_OF_B" &
SERVER_PIDS+=($!)
sleep 0.5
expect_exit 17 wtenv doctor --json
printf '%s' "$OUT" | jget 'sorted({f["code"] for f in d["findings"]})' | grep -q port_conflict || fail "no port_conflict"
ok "a foreign process on an assigned port: doctor exits 17 with port_conflict (US6 scenario 4)"
cd "$WORK"
```

## 9. Clean up

```bash
for wt in app-a app-b app-c app-hook app-hookfail; do
  (cd "$WORK/$wt" && wtenv down >/dev/null)
done
(cd "$WORK/app" && wtenv down >/dev/null) || true
for wt in plain-b plain-c par-1 par-2 par-3 par-4 par-5 app-bare; do
  (cd "$WORK/$wt" && wtenv down >/dev/null) || true
done
[ "$(wtenv ls --json | jget 'len([w for w in d["worktrees"] if w["status"] != "unprovisioned"])')" = 0 ] \
  || fail "registry not empty after cleanup"
psql_q -d postgres -c "DROP DATABASE wtenv_decoy_00000000"
docker volume rm wtenv-decoy-vol >/dev/null
ok "all worktrees released; registry empty"
echo "ACCEPTANCE PASSED"
```

---

## Measuring startup time (NFR-001, SC-004)

Not part of the acceptance script. Run on the maintainer's machine, with the installed
`wtenv`, from the root of a repository that has only its main worktree, with an empty
registry, and record the result in `docs/benchmarks.md` with the machine model, OS version,
and wtenv version.

```text
export XDG_STATE_HOME="$(mktemp -d)"
hyperfine --warmup 5 --runs 30 'wtenv --version'
hyperfine --warmup 5 --runs 30 'wtenv ls --json'
```

Pass: each reported mean is under 300 ms.

## Provisioning time (NFR-002, SC-002)

Also outside the script: time a first `up` in a new worktree of the sample app with
`[database]` and `post_up` removed from `wtenv.toml`, and a repeat `up` in a provisioned
worktree with the full configuration and `post_up` removed. Both must take under 5 seconds.

## Working with Claude Code

Claude Code switches git hooks off when it creates a worktree (research.md, section 2), so
the hook does not provision worktrees made by `claude --worktree`. Add this line to the
project's `CLAUDE.md`:

```text
Before starting servers or running migrations in a new worktree, run `wtenv up` in it.
```

Do not list wtenv's env file in `.worktreeinclude`: the copy would carry the main
checkout's ports until `wtenv up` rewrites it. Worktrees that Claude Code removes are
reclaimed by `wtenv gc`.
