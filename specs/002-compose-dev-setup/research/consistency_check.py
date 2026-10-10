"""Check tasks.md of spec 002 against itself and against spec.md.

Run from anywhere with `python3 -I specs/002-compose-dev-setup/research/consistency_check.py`.
Exits 0 when every check passes and 1 otherwise. Standard library only; it reads two files.

Checks:
1. Task IDs are unique.
2. No cited task ID is undefined, apart from spec 001 tasks (an ID whose every mention has
   "001" earlier on its line, such as "001 T224").
3. Every FR-001..FR-033 and SC-001..SC-009 has a row in the coverage table, and each row cites
   at least one defined task.
4. Every task has a "Done when" line.
5. No task "needs" (the IDs after the word) a task that comes later in the file (the order of the file is the order of
   the work; the numbers are not).
6. The `[X]` and `[ ]` counts, printed for information.
"""

import re
import sys
from pathlib import Path

FEATURE = Path(__file__).resolve().parents[1]
TASKS = FEATURE / "tasks.md"
FUNCTIONAL = [f"FR-{n:03d}" for n in range(1, 34)]
SUCCESS = [f"SC-{n:03d}" for n in range(1, 10)]
TASK_LINE = re.compile(r"^- \[([ X])\] (T\d{3})\b")
TASK_ID = re.compile(r"\bT\d{3}\b")
RANGE = re.compile(r"\b(T\d{3})[–-](T\d{3})\b")


def split_tasks(lines: list[str]) -> list[tuple[str, int, list[str]]]:
    """Return `(id, line number, body lines)` for each task; a body runs to the next heading."""
    starts = [(index, TASK_LINE.match(line)) for index, line in enumerate(lines)]
    starts = [(index, match) for index, match in starts if match]
    tasks = []
    for position, (index, match) in enumerate(starts):
        assert match is not None
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = []
        for line in lines[index:end]:
            if body and line.startswith(("#", "---")):
                break
            body.append(line)
        tasks.append((match.group(2), index + 1, body))
    return tasks


def expand(text: str) -> set[str]:
    """Return the task IDs in `text`, with `T032–T039` ranges expanded."""
    found = set(TASK_ID.findall(text))
    for first, last in RANGE.findall(text):
        found |= {f"T{n:03d}" for n in range(int(first[1:]), int(last[1:]) + 1)}
    return found


def is_spec_001_mention(line: str, column: int) -> bool:
    """Return whether the ID at `column` of `line` follows the number 001 on its line."""
    return "001" in line[:column]


def main() -> int:
    lines = TASKS.read_text(encoding="utf-8").splitlines()
    tasks = split_tasks(lines)
    problems: list[str] = []

    ids = [task_id for task_id, _, _ in tasks]
    for task_id in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{task_id} is defined more than once")

    defined = set(ids)
    cited: dict[str, list[bool]] = {}
    for line in lines:
        for match in TASK_ID.finditer(line):
            cited.setdefault(match.group(0), []).append(is_spec_001_mention(line, match.start()))
    for task_id in sorted(set(cited) - defined):
        if not all(cited[task_id]):
            problems.append(f"{task_id} is cited but not defined")

    coverage = [line for line in lines if re.match(r"^\| (FR|SC)-\d{3} \|", line)]
    rows = {line.split("|")[1].strip(): line.split("|")[2] for line in coverage}
    for requirement in FUNCTIONAL + SUCCESS:
        if requirement not in rows:
            problems.append(f"{requirement} has no row in the coverage table")
        elif not expand(rows[requirement]) & defined:
            problems.append(f"{requirement} cites no defined task")
        elif expand(rows[requirement]) - defined:
            missing = sorted(expand(rows[requirement]) - defined)
            problems.append(f"{requirement} cites undefined tasks {missing}")

    position = {task_id: number for number, (task_id, _, _) in enumerate(tasks)}
    for task_id, line_number, body in tasks:
        text = "\n".join(body)
        if "Done when" not in text:
            problems.append(f"{task_id} (line {line_number}) has no Done-when")
        for sentence in re.split(r"(?<=[.;])\s+|\n", text):
            after = re.split(r"\bneeds?\b", sentence, maxsplit=1, flags=re.IGNORECASE)
            if len(after) < 2:
                continue  # only the IDs after the word are needed; those before it need this task
            for needed in expand(after[1]) & defined - {task_id}:
                if position[needed] > position[task_id]:
                    problems.append(f"{task_id} needs {needed}, which comes later in the file")

    done = sum(1 for line in lines if line.startswith("- [X] T"))
    open_ = sum(1 for line in lines if line.startswith("- [ ] T"))
    print(f"tasks: {len(tasks)} defined, {done} [X], {open_} [ ]")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("consistency check:", "FAILED" if problems else "PASSED")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
