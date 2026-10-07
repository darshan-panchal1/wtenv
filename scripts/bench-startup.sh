#!/usr/bin/env bash
# Startup benchmark (NFR-001, SC-004): `wtenv --version` and `wtenv ls --json` must each have
# a mean under 300 ms.
#
# Runs the installed `wtenv` directly (not `uv run`), from the root of a repository that has
# only its main worktree, with an empty registry (XDG_STATE_HOME in a temporary directory).
# Install the build under test first:  uv tool install --force --reinstall .
# Exits non-zero when a mean is 300 ms or more.
set -euo pipefail

LIMIT_SECONDS=0.3

command -v hyperfine >/dev/null || { echo "hyperfine is not installed" >&2; exit 2; }
command -v wtenv >/dev/null || { echo "wtenv is not on PATH" >&2; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/wtenv-bench.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

export XDG_STATE_HOME="$WORK/state"
export GIT_AUTHOR_NAME=bench GIT_AUTHOR_EMAIL=bench@example.invalid
export GIT_COMMITTER_NAME=bench GIT_COMMITTER_EMAIL=bench@example.invalid
git init -q -b main "$WORK/repo"
git -C "$WORK/repo" commit -q --allow-empty -m init
cd "$WORK/repo"

echo "wtenv: $(command -v wtenv) ($(wtenv --version))"

status=0
for command in 'wtenv --version' 'wtenv ls --json'; do
  hyperfine --warmup 5 --runs 30 --export-json "$WORK/result.json" "$command"
  mean=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["results"][0]["mean"])' "$WORK/result.json")
  if python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) < float(sys.argv[2]) else 1)' "$mean" "$LIMIT_SECONDS"; then
    echo "ok - '$command' mean ${mean} s is under ${LIMIT_SECONDS} s"
  else
    echo "FAIL: '$command' mean ${mean} s is not under ${LIMIT_SECONDS} s" >&2
    status=1
  fi
done
exit "$status"
