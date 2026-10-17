#!/bin/bash
# E1.6: duplicate and near-duplicate port entries across the compose file and the repository override
set -u
LABEL="$1"; CFG="$2"
S="${RESEARCH_DIR:?set RESEARCH_DIR to a scratch directory holding fastapi-template/}"
D="$S/research/work-$LABEL/wtenv-research-e1c"; rm -rf "$D"; mkdir -p "$D"
export DOCKER_HOST="unix://$HOME/.docker/run/docker.sock"
if [ -n "$CFG" ]; then export DOCKER_CONFIG="$CFG"; fi
show() { echo; echo "\$ $*"; ( set -o pipefail; eval "$@" ) 2>&1; echo "[exit $?]"; }
printf 'services:\n  web:\n    image: valkey/valkey:latest\n    ports:\n      - "18001:6379"\n' > "$D/compose.yaml"
printf 'services:\n  web:\n    ports:\n      - "18001:6379"\n' > "$D/same.yaml"
printf 'services:\n  web:\n    ports:\n      - "18002:6379"\n' > "$D/other.yaml"
echo "=== $LABEL: $(docker compose version)"
echo; echo "## E1.6a the override repeats the compose file's entry exactly"
show "cd $D && docker compose -p wtenv-research-e1c -f compose.yaml -f same.yaml config --format json | jq -c '[.services.web.ports[] | {target, published}]'"
echo; echo "## E1.6b the override publishes the same container port on another host port"
show "cd $D && docker compose -p wtenv-research-e1c -f compose.yaml -f other.yaml config --format json | jq -c '[.services.web.ports[] | {target, published}]'"
