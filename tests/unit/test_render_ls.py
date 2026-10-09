"""The text `wtenv ls` prints for people: the published ports (cli.md, `wtenv ls`; T231; FR-031)."""

from wtenv.output import BlockView, LsResult, PortView, Status, WorktreeView, render_ls_text

SIX_COLUMNS = ["STATUS", "PORTS", "VARIABLES", "DATABASE", "COMPOSE", "PATH"]


def worktree(path: str, start: int, ports: list[PortView]) -> WorktreeView:
    return WorktreeView(
        repository="/code/app/.git",
        git_dir=f"/code/app/.git/worktrees/{start}",
        path=path,
        status=Status.PROVISIONED,
        block=BlockView(start=start, end=start + 9, size=10),
        ports=ports,
        compose_project="wtenv-app-1",
    )


def render(*views: WorktreeView) -> list[str]:
    return render_ls_text(LsResult(ok=True, worktrees=list(views))).splitlines()


def test_published_ports_show_with_their_service_and_container_port() -> None:
    lines = render(
        worktree(
            "/code/app",
            20000,
            [
                PortView(port=20000, variable="PORT"),
                PortView(port=20001, service="backend", target=8000, protocol="tcp"),
                PortView(port=20002, service="frontend", target=5173, protocol="tcp"),
            ],
        )
    )

    header, row = lines
    assert header.split() == [
        "STATUS",
        "PORTS",
        "VARIABLES",
        "PUBLISHED",
        "DATABASE",
        "COMPOSE",
        "PATH",
    ]
    assert "PORT=20000" in row
    assert "backend:8000->20001" in row
    assert "frontend:5173->20002" in row


def test_a_published_port_tied_to_a_variable_shows_in_both_columns() -> None:
    lines = render(
        worktree(
            "/code/app",
            20000,
            [
                PortView(port=20000, variable="PORT"),
                PortView(
                    port=20001, variable="CACHE_PORT", service="cache", target=6379, protocol="tcp"
                ),
            ],
        )
    )

    row = lines[1]
    assert "PORT=20000 CACHE_PORT=20001" in row
    assert "cache:6379->20001" in row


def test_a_port_that_is_not_tcp_shows_its_protocol() -> None:
    lines = render(
        worktree(
            "/code/app",
            20000,
            [
                PortView(port=20000, variable="PORT"),
                PortView(port=20001, service="dns", target=53, protocol="udp"),
            ],
        )
    )

    assert "dns:53/udp->20001" in lines[1]


def test_each_row_keeps_its_own_published_ports_under_the_one_column() -> None:
    lines = render(
        worktree(
            "/code/one",
            20000,
            [
                PortView(port=20000, variable="PORT"),
                PortView(port=20001, service="backend", target=8000, protocol="tcp"),
            ],
        ),
        worktree("/code/two", 20010, [PortView(port=20010, variable="PORT")]),
    )

    header, first, second = lines
    column = header.index("PUBLISHED")
    assert first[column:].startswith("backend:8000->20001")
    assert second[column:].startswith("-")
    assert first.endswith("/code/one") and second.endswith("/code/two")


def test_without_a_published_port_the_table_has_the_six_columns_of_before() -> None:
    lines = render(worktree("/code/app", 20000, [PortView(port=20000, variable="PORT")]))

    assert lines[0].split() == SIX_COLUMNS
    assert "PUBLISHED" not in lines[0]
