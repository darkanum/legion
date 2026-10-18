"""Reads a GitHub file patch: the right-side line number of each row, for Gate 0 and for the inline comment check."""

import re
from typing import Iterator

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def right_rows(patch: str | None) -> Iterator[tuple[int, str]]:
    """(right-side line number, raw row) for every row that is on the right side: added and context rows."""
    number = 0
    for row in (patch or "").splitlines():
        hunk = HUNK.match(row)
        if hunk:
            number = int(hunk.group(1))
            continue
        if row.startswith("-") or row.startswith("\\"):
            continue
        yield number, row
        number += 1
