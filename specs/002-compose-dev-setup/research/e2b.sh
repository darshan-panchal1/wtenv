#!/bin/bash
# E2.13: does COMPOSE_PROJECT_DIRECTORY make a child in a subdirectory read the project's .env?
set -u
LABEL="$1"; CFG="$2"
S="${RESEARCH_DIR:?set RESEARCH_DIR to a scratch directory holding fastapi-template/}"
W="$S/research/work-$LABEL"; E2="$W/wtenv-research-e2"
export DOCKER_HOST="unix://$HOME/.docker/run/docker.sock"
if [ -n "$CFG" ]; then export DOCKER_CONFIG="$CFG"; fi
CF="$E2/compose.yaml:$E2/compose.override.yaml:$E2/.wtenv/override.yaml"
summary="jq -c '{name, web: [.services.web.ports[] | .published], dotenv: .services.api.environment.FROM_DOT_ENV}'"
show() { echo; echo "\$ $*"; ( set -o pipefail; eval "$@" ) 2>&1; echo "[exit $?]"; }
echo "=== $LABEL: $(docker compose version)"
echo; echo "## E2.13 COMPOSE_PROJECT_DIRECTORY beside COMPOSE_FILE, from a subdirectory"
show "cd $E2/backend && COMPOSE_FILE=$CF COMPOSE_PROJECT_DIRECTORY=$E2 docker compose config --format json | $summary"
echo; echo "## E2.14 --project-directory flag, from a subdirectory"
show "cd $E2/backend && COMPOSE_FILE=$CF docker compose --project-directory $E2 config --format json | $summary"
echo; echo "## E2.15 no COMPOSE_FILE, plain command from a subdirectory (Compose finds the files by walking up)"
show "cd $E2/backend && docker compose config --format json | $summary"
