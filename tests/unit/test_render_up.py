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
