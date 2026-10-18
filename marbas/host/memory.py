"""Marbas memory v1: one Markdown note per engineer Reject or PR change request.

The folder is an Obsidian-style vault on the agents host. The sandbox reads it read-only at /memory during --plan.
Only an engineer's words become a note; the issue text never does.
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import paths  # noqa: E402

MEMORY = paths.memory("marbas")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def clean(text: str) -> str:
    # A note must never hold an email address; engineers can paste one in a reply.
    return EMAIL.sub("[email]", text).strip()


def write_note(kind: str, issue: str, packages: list[str], engineer: str, words: str, lesson: str,
               source: str, folder: Path = MEMORY) -> Path:
    """kind is "plan-reject" or "pr-changes". Returns the note path."""
    folder.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = folder / f"{day}-issue-{issue}-{kind}.md"
    n = 2
    while path.exists():
        path = folder / f"{day}-issue-{issue}-{kind}-{n}.md"
        n += 1
    package_list = ", ".join(f'"{p}"' for p in packages)
    words_block = "\n".join(f"> {line}" for line in clean(words).splitlines()) or "> (no text)"
    path.write_text(
        f"---\nkind: {kind}\nissue: {issue}\npackages: [{package_list}]\nengineer: {engineer}\ndate: {day}\n---\n\n"
        f"# Lesson\n\n{clean(lesson)}\n\n"
        f"## What the engineer said\n\n{words_block}\n\n"
        f"## Source\n\n{clean(source)}\n\n"
        f"Related: [[issue-{issue}]]\n",
        encoding="utf-8",
    )
    return path
