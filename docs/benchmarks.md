# Benchmarks

## Startup time (NFR-001, SC-004)

Measured with `scripts/bench-startup.sh`: the installed `wtenv` executable (installed with
`uv tool install --force --reinstall .`), called directly, from the root of a repository that
has only its main worktree, with an empty registry. Each command runs under
`hyperfine --warmup 5 --runs 30`. The budget is a mean under 300 ms.

| Date | Machine | OS | wtenv | Command | Mean | Std. dev. | Budget |
|------|---------|----|-------|---------|------|-----------|--------|
| 2026-10-06 | Mac17,3 (Apple M5), arm64 | macOS 27.0 (26A428) | 0.1.0 | `wtenv --version` | 57.7 ms | 1.1 ms | pass |
| 2026-10-06 | Mac17,3 (Apple M5), arm64 | macOS 27.0 (26A428) | 0.1.0 | `wtenv ls --json` | 212.2 ms | 1.6 ms | pass |

Tools: hyperfine 1.20.0, uv 0.11.23.

Repeat this before each release and add the rows here.
