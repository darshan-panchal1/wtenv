"""`up` refuses `[database] type = "compose"` cleanly until T086 provisions it (T013, T086).

Spec 002 accepts the configuration (config.md), but no code provisions the database yet. The
interim guard in `provision._database_plan` must be a `WtenvError` with a stable code, so the
command prints its error and exits with that code, never a Python traceback. T086 removes the
guard and this file. No Docker is needed.
"""

from pathlib import Path

import pytest

from wtenv import provision
from wtenv.config import load_config
from wtenv.errors import EXIT_STATUS, ErrorCode, WtenvError
from wtenv.identity import WorktreeIdentity

GIT_DIR = "/repos/app/.git/worktrees/feature-x"
TOML = (
    '[compose]\nfile = "compose.yaml"\n\n'
    '[database]\ntype = "compose"\nservice = "db"\n'
    'url = "postgresql://postgres:{compose_env:POSTGRES_PASSWORD}@localhost:{port}/app"\n'
)


def test_up_refuses_a_compose_database_with_a_stable_error_not_a_traceback(
    tmp_path: Path,
) -> None:
    (tmp_path / "wtenv.toml").write_text(TOML, encoding="utf-8")
    config = load_config(tmp_path)
    identity = WorktreeIdentity(git_dir=GIT_DIR, path=str(tmp_path), repository="/repos/app/.git")

    with pytest.raises(WtenvError) as raised:
        provision._database_plan(tmp_path, identity, config, None)

    error = raised.value
    assert error.code is ErrorCode.UNSUPPORTED
    assert EXIT_STATUS[error.code] == 19
    assert error.details == {"reason": "compose_database_not_provisioned", "service": "db"}
    assert "compose-hosted database" in error.message
    assert error.hint
