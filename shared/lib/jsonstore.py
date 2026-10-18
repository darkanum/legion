"""Small JSON state files that several threads write and another thread reads.

`write_text` truncates in place, so a reader that lands mid-write gets an empty or half-written file and raises. Here a
write goes to a temporary file in the same folder and is renamed, which is atomic, and a read answers None for a file it
cannot parse, so one bad file never stops a whole scan.
"""

import json
import os
import tempfile
from pathlib import Path


def write(path: Path, data: object, indent: int | None = None) -> Path:
    """Writes to a temporary name in the same folder, then renames, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(data, file, indent=indent)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read(path: Path) -> dict | None:
    """The document, or None when the file is gone or does not parse. The caller decides what None means."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
