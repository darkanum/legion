"""The context the host prepares before a run, so each model needs fewer turns: the touched files, the guide files
(AGENTS.md, CLAUDE.md) on their paths, and a consumer count for each new exported name.

It reads a bare clone that stays on the VM and only fetches what changed, so a run never clones the repository again.
The container mounts the same clone read-only and uses it as a git reference.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
import github as gh  # noqa: E402
import paths  # noqa: E402

CACHE = paths.workspace("orobas") / "repo.git"
MAX_FILE_BYTES = 200_000
MAX_NAMES = 30
# The files that tell an agent how a folder works, read from the root down to each touched folder.
GUIDES = config.items("LEGION_GUIDE_FILES", "AGENTS.md,CLAUDE.md")
# A new exported name in an added line: JavaScript and TypeScript `export`, a capitalised Go func or type, and a
# top-level Python def or class.
EXPORTS = (
    re.compile(r"^\+\s*export\s+(?:default\s+)?(?:async\s+)?(?:const|let|function|class|type|interface|enum)\s+([A-Za-z_]\w{2,})"),
    re.compile(r"^\+func\s+(?:\([^)]*\)\s+)?([A-Z]\w{2,})\s*[\[(]"),
    re.compile(r"^\+type\s+([A-Z]\w{2,})\s"),
    re.compile(r"^\+(?:async\s+)?(?:def|class)\s+([A-Za-z]\w{2,})"),
)


def git(*args: str, cwd: Path = CACHE) -> str:
    # No hooks: the objects come from any PR author.
    done = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, check=True, capture_output=True)
    return done.stdout.decode("utf-8", "replace")


def refresh(number: int | None = None) -> None:
    """Creates the bare clone once, then fetches the base branch, and the PR head when a number is given."""
    header = gh.git_auth()
    url = f"https://github.com/{gh.REPO}.git"
    if not (CACHE / "HEAD").exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        git("-c", header, "clone", "-q", "--bare", url, str(CACHE), cwd=CACHE.parent)
    base = config.BASE_BRANCH
    refs = [f"+refs/heads/{base}:refs/heads/{base}"]
    if number is not None:
        refs.append(f"+refs/pull/{number}/head:refs/pull/{number}/head")
    git("-c", header, "fetch", "-q", "--prune", url, *refs)


def show(sha: str, path: str) -> str | None:
    try:
        return git("show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def agents_files(paths: list[str], guides: tuple[str, ...] = GUIDES) -> list[str]:
    """Every guide file from the root down to the folder of each touched path, root first."""
    folders = {""}
    for path in paths:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            folders.add("/".join(parts[:depth]) + "/")
    found = {f"{folder}{guide}" for folder in folders for guide in guides}
    return sorted(found, key=lambda p: (p.count("/"), p))


def new_exports(files: list[dict]) -> dict[str, str]:
    """{name: defining path} for each exported name the diff adds, at most MAX_NAMES."""
    names: dict[str, str] = {}
    for f in files:
        for row in (f.get("patch") or "").splitlines():
            match = next((m for pattern in EXPORTS if (m := pattern.match(row))), None)
            if match and len(names) < MAX_NAMES:
                names.setdefault(match.group(1), f["filename"])
    return names


def consumers(sha: str, names: dict[str, str]) -> dict[str, dict]:
    """{name: {definedIn, files}}: the files at the head that use the name, outside the file that defines it."""
    counts = {}
    for name, defined_in in names.items():
        try:
            hits = git("grep", "-l", "-w", "-F", name, sha, "--", ".", ":!*.md").splitlines()
        except subprocess.CalledProcessError:
            hits = []  # git grep exits 1 on no match
        users = sorted({hit.split(":", 1)[-1] for hit in hits} - {defined_in})
        counts[name] = {"definedIn": defined_in, "files": users[:20], "count": len(users)}
    return counts


def build(folder: Path, sha: str, files: list[dict]) -> None:
    """Writes files/, agents.md and consumers.json into the run inputs."""
    root = folder / "files"
    for f in files:
        name = f["filename"]
        if f.get("status") == "removed" or name.startswith("/") or ".." in name.split("/"):
            continue
        text = show(sha, f["filename"])
        if text is None or len(text) > MAX_FILE_BYTES or "\x00" in text:
            continue
        target = root / f["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    sections = [f"# {path}\n\n{text}" for path in agents_files([f["filename"] for f in files])
                if (text := show(sha, path)) is not None]
    (folder / "agents.md").write_text("\n\n".join(sections))
    (folder / "consumers.json").write_text(json.dumps(consumers(sha, new_exports(files)), indent=1))


if __name__ == "__main__":
    refresh()  # setup.sh primes the clone once, so the first review does not pay for it
