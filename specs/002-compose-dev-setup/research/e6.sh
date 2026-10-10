#!/bin/bash
# E6: the three Compose claims of the spec 002 safety review (findings M2 and M5).
# Usage: e6.sh <label> <docker-config-dir or "">
# Only `docker compose config` runs: no container, volume or network is created. Every project
# name is wtenv-research-e6*, and the work directory is removed at the end.
set -u
LABEL="$1"; CFG="$2"
S="${RESEARCH_DIR:?set RESEARCH_DIR to a scratch directory}"
W="$S/research/work-e6-$LABEL"; rm -rf "$W"; mkdir -p "$W"
HERE="$(cd "$(dirname "$0")" && pwd)"
export DOCKER_HOST="unix://$HOME/.docker/run/docker.sock"
if [ -n "$CFG" ]; then export DOCKER_CONFIG="$CFG"; fi
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PATH_SEPARATOR COMPOSE_ENV_FILES COMPOSE_PROFILES

show() { echo; echo "\$ $*"; ( set -o pipefail; eval "$@" ) 2>&1; echo "[exit $?]"; }
summary="jq -c '{name, web: [.services.web.ports[]? | .published], dotenv: .services.web.environment.FROM_DOT_ENV}'"

echo "=== $LABEL: $(docker compose version)"

# ------------------------------------------------- E6.1: `.env` line forms that set the project name
# The compose file sets `name:`, as wtenv's isolation file does; COMPOSE_FILE is set as `exec` sets it.
A="$W/wtenv-research-e6a"; mkdir -p "$A"
cat > "$A/compose.yaml" <<'EOF'
name: wtenv-research-e6-iso
services:
  web:
    image: valkey/valkey:latest
    environment:
      FROM_DOT_ENV: ${FROM_DOT_ENV:-unset}
EOF
printf 'services:\n  web:\n    ports:\n      - "18099:6379"\n' > "$A/other.yaml"
form() { # $1 = case label, $2 = the .env bytes (printf format)
  printf "$2" > "$A/.env"
  echo; echo "## E6.1 $1: .env is $(od -An -c "$A/.env" | tr -s ' ' | head -c 120)"
  show "cd $A && COMPOSE_FILE=$A/compose.yaml docker compose config --format json | $summary"
  show "python3 -I $HERE/e6_pattern.py $A/.env"
}
form "a1 KEY=value (control)"          'COMPOSE_PROJECT_NAME=wtenv-research-e6-eq\n'
form "a2 KEY: value"                   'COMPOSE_PROJECT_NAME: wtenv-research-e6-colon\n'
form "a3 KEY:value"                    'COMPOSE_PROJECT_NAME:wtenv-research-e6-colon-nospace\n'
form "a4 export KEY: value"            'export COMPOSE_PROJECT_NAME: wtenv-research-e6-export-colon\n'
form "a5 indented KEY : value"         '   COMPOSE_PROJECT_NAME : wtenv-research-e6-spaced-colon\n'
form "a6 UTF-8 BOM, then KEY=value"    '\357\273\277COMPOSE_PROJECT_NAME=wtenv-research-e6-bom\n'
form "a7 CRLF line end, KEY: value"    'COMPOSE_PROJECT_NAME: wtenv-research-e6-crlf\r\n'
form "a8 COMPOSE_FILE: other.yaml (no COMPOSE_FILE in the environment)" 'COMPOSE_FILE: other.yaml\n'
echo; echo "## E6.1 a8b the same .env, plain command with no COMPOSE_FILE in the environment"
show "cd $A && docker compose config --format json | $summary"

# ------------------------------------------------- E6.2: both compose.override.yml and .yaml exist
B="$W/wtenv-research-e6b"; mkdir -p "$B"
cat > "$B/compose.yaml" <<'EOF'
name: wtenv-research-e6b
services:
  web:
    image: valkey/valkey:latest
    ports:
      - "18001:6379"
EOF
printf 'services:\n  web:\n    ports:\n      - "18002:6379"\n' > "$B/compose.override.yml"
printf 'services:\n  web:\n    ports:\n      - "18003:6379"\n' > "$B/compose.override.yaml"
echo; echo "## E6.2a compose.yaml + compose.override.yml (18002) + compose.override.yaml (18003); stderr shown after the JSON"
show "cd $B && docker compose config --format json 2>$B/stderr.txt | $summary; echo '--- stderr:'; cat $B/stderr.txt"
printf 'services:\n  web:\n    ports:\n      - "18004:6379"\n' > "$B/docker-compose.override.yml"
printf 'services:\n  web:\n    ports:\n      - "18005:6379"\n' > "$B/docker-compose.override.yaml"
echo; echo "## E6.2b all four override names (yml 18002, yaml 18003, docker-compose yml 18004, docker-compose yaml 18005)"
show "cd $B && docker compose config --format json 2>$B/stderr.txt | $summary; echo '--- stderr:'; cat $B/stderr.txt"

# ------------------------------------------------- E6.3: COMPOSE_ENV_FILES and the current directory's .env
C="$W/wtenv-research-e6c"; mkdir -p "$C/sub"
cat > "$C/compose.yaml" <<'EOF'
name: wtenv-research-e6c-iso
services:
  web:
    image: valkey/valkey:latest
    environment:
      FROM_DOT_ENV: ${FROM_DOT_ENV:-unset}
      ONLY_PROJECT: ${ONLY_PROJECT:-unset}
EOF
printf 'FROM_DOT_ENV=project\nONLY_PROJECT=yes\n' > "$C/.env"
summary="jq -c '{name, dotenv: .services.web.environment.FROM_DOT_ENV, only_project: .services.web.environment.ONLY_PROJECT}'"
printf 'COMPOSE_PROJECT_NAME=wtenv-research-e6c-subdir\nFROM_DOT_ENV=subdir\n' > "$C/sub/.env"
printf 'COMPOSE_PROJECT_NAME=wtenv-research-e6c-envfiles\nFROM_DOT_ENV=envfiles\n' > "$C/named.env"
echo; echo "## E6.3a from sub/, whose .env sets COMPOSE_PROJECT_NAME; COMPOSE_FILE only"
show "cd $C/sub && COMPOSE_FILE=$C/compose.yaml docker compose config --format json | $summary"
echo; echo "## E6.3b the same, plus COMPOSE_ENV_FILES=<project>/.env (absolute)"
show "cd $C/sub && COMPOSE_FILE=$C/compose.yaml COMPOSE_ENV_FILES=$C/.env docker compose config --format json | $summary"
echo; echo "## E6.3c the same, plus COMPOSE_ENV_FILES=/dev/null (no project .env)"
show "cd $C/sub && COMPOSE_FILE=$C/compose.yaml COMPOSE_ENV_FILES=/dev/null docker compose config --format json | $summary"
echo; echo "## E6.3d from the project directory, COMPOSE_ENV_FILES names a file that sets COMPOSE_PROJECT_NAME"
show "cd $C && COMPOSE_FILE=$C/compose.yaml COMPOSE_ENV_FILES=$C/named.env docker compose config --format json | $summary"
echo; echo "## E6.3e from sub/, plain command, no COMPOSE_FILE (Compose finds compose.yaml by walking up)"
show "cd $C/sub && docker compose config --format json | $summary"
echo; echo "## E6.3f control: from sub/ after removing sub/.env, COMPOSE_FILE only"
rm "$C/sub/.env"
show "cd $C/sub && COMPOSE_FILE=$C/compose.yaml docker compose config --format json | $summary"

rm -rf "$W"
