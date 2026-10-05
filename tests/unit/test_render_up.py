"""The text `wtenv up` prints for people: the database line (cli.md, `wtenv up`; T066; FR-019)."""

from wtenv.output import (
    BlockView,
    DatabaseView,
    Item,
    ItemKind,
    PortView,
    ResourceState,
    Status,
    UpChange,
    UpResult,
    WorktreeView,
    render_up_text,
)


def result_with(database: DatabaseView, item: Item, action: str) -> UpResult:
    worktree = WorktreeView(
        repository="/code/app/.git",
        git_dir="/code/app/.git/worktrees/feature-x",
        path="/code/feature-x",
        status=Status.PROVISIONED,
        block=BlockView(start=20010, end=20019, size=10),
        ports=[PortView(port=20010, variable="PORT")],
        databases=[database],
        env_file=".env.local",
    )
    return UpResult(
        ok=True,
        worktree=worktree,
        changes=[UpChange(item=item, action=action)],  # type: ignore[arg-type]
    )


def test_a_postgres_database_shows_kind_name_host_and_port_and_what_happened() -> None:
    database = DatabaseView(
        kind="postgres",
        state=ResourceState.CREATED,
        name="wtenv_feature_x_3f9a1c2b",
        host="localhost",
        port=5432,
    )
    item = Item(kind=ItemKind.POSTGRES_DATABASE, name="wtenv_feature_x_3f9a1c2b")

    text = render_up_text(result_with(database, item, "created"))

    assert "  database   postgres wtenv_feature_x_3f9a1c2b on localhost:5432  (created)" in text


def test_a_sqlite_database_shows_the_path_of_the_copy() -> None:
    path = "/code/feature-x/.wtenv/dev.sqlite3"
    database = DatabaseView(kind="sqlite", state=ResourceState.CREATED, path=path)
    item = Item(kind=ItemKind.SQLITE_FILE, name=path)

    text = render_up_text(result_with(database, item, "unchanged"))

    assert f"  database   sqlite {path}  (unchanged)" in text


def test_the_database_line_comes_after_the_ports_and_before_the_env_file() -> None:
    path = "/code/feature-x/.wtenv/dev.sqlite3"
    database = DatabaseView(kind="sqlite", state=ResourceState.CREATED, path=path)
    item = Item(kind=ItemKind.SQLITE_FILE, name=path)

    lines = render_up_text(result_with(database, item, "created")).splitlines()

    kinds = [line.split()[0] for line in lines]
    assert kinds.index("ports") < kinds.index("database") < kinds.index("env")


def test_no_line_shows_a_url() -> None:
    database = DatabaseView(
        kind="postgres", state=ResourceState.CREATED, name="db", host="localhost", port=5432
    )
    item = Item(kind=ItemKind.POSTGRES_DATABASE, name="db")

    text = render_up_text(result_with(database, item, "created"))

    assert "postgresql://" not in text and "DATABASE_URL" not in text


# --- published ports and the compose project (cli.md, `wtenv up`; T084) ------------------------

PROJECT = "wtenv-feature-x-3f9a1c2b"
OVERRIDE = "/code/feature-x/compose.override.yaml"


def compose_result(
    ports: list[PortView], override_action: str | None = "created", project: str | None = PROJECT
) -> UpResult:
    worktree = WorktreeView(
        repository="/code/app/.git",
        git_dir="/code/app/.git/worktrees/feature-x",
        path="/code/feature-x",
        status=Status.PROVISIONED,
        block=BlockView(start=20010, end=20019, size=10),
        ports=ports,
        compose_project=project,
        env_file=".env.local",
    )
    changes = []
    if override_action is not None:
        item = Item(kind=ItemKind.COMPOSE_OVERRIDE, name=OVERRIDE)
        changes.append(UpChange(item=item, action=override_action))  # type: ignore[arg-type]
    return UpResult(ok=True, worktree=worktree, changes=changes)


TIED_AND_UNTIED = [
    PortView(port=20010, variable="PORT"),
    PortView(port=20011, variable="DB_PORT", service="db", target=5432, protocol="tcp"),
    PortView(port=20012, service="cache", target=6379, protocol="tcp"),
]


def test_published_ports_are_listed_with_the_variable_they_are_tied_to() -> None:
    text = render_up_text(compose_result(TIED_AND_UNTIED))

    assert "  published  db:5432 -> 20011 (DB_PORT)  cache:6379 -> 20012" in text.splitlines()


def test_a_port_that_is_not_tcp_shows_its_protocol() -> None:
    ports = [PortView(port=20011, service="dns", target=53, protocol="udp")]

    text = render_up_text(compose_result(ports))

    assert "  published  dns:53/udp -> 20011" in text.splitlines()


def test_the_compose_line_shows_the_project_the_override_and_what_happened() -> None:
    text = render_up_text(compose_result(TIED_AND_UNTIED))

    assert f"  compose    {PROJECT}  compose.override.yaml (created)" in text.splitlines()


def test_the_compose_line_follows_what_happened_to_the_override() -> None:
    text = render_up_text(compose_result(TIED_AND_UNTIED, "unchanged"))

    assert f"  compose    {PROJECT}  compose.override.yaml (unchanged)" in text.splitlines()


def test_published_comes_after_ports_and_compose_before_the_env_file() -> None:
    lines = render_up_text(compose_result(TIED_AND_UNTIED)).splitlines()

    kinds = [line.split()[0] for line in lines]
    assert kinds.index("ports") < kinds.index("published") < kinds.index("compose")
    assert kinds.index("compose") < kinds.index("env")


def test_without_compose_there_is_no_published_and_no_compose_line() -> None:
    result = compose_result([PortView(port=20010, variable="PORT")], None, None)

    kinds = [line.split()[0] for line in render_up_text(result).splitlines()]

    assert "published" not in kinds and "compose" not in kinds


def test_a_project_that_stays_recorded_without_an_override_shows_no_override() -> None:
    result = compose_result([PortView(port=20010, variable="PORT")], "released")

    lines = render_up_text(result).splitlines()

    assert f"  compose    {PROJECT}  (no override)" in lines
    assert f"  released   compose_override {OVERRIDE}" in lines
