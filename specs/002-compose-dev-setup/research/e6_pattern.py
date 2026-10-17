"""E6.1 helper: which `.env` lines wtenv's check would see, with v0.1.1's pattern and with `[=:]`.

Usage: python3 -I e6_pattern.py <.env file>. Reads the file the way `compose.check_environment`
does (UTF-8, errors replaced, `splitlines`), and prints, per pattern, the variable it found or
`-`. The v0.1.1 pattern is copied from `src/wtenv/compose.py` (`_ENV_ASSIGNMENT`).
"""

import re
import sys

V011 = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
COLON = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)")
COLON_BOM = re.compile(r"﻿?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)")
WATCHED = ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE")


def found(pattern: re.Pattern[str], text: str) -> str:
    """Return the watched variable the pattern finds with a non-empty value, or `-`."""
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match is not None and match.group(1) in WATCHED and match.group(2).strip():
            return match.group(1)
    return "-"


def main() -> None:
    """Print what each pattern finds in the file given as the only argument."""
    with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    print(f"v0.1.1 '=' only: {found(V011, text)}")
    print(f"'[=:]':          {found(COLON, text)}")
    print(f"'[=:]' + BOM:    {found(COLON_BOM, text)}")


main()
