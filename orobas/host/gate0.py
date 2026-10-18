"""Gate 0: the rules a host can decide from pr.json and the diff text alone, with no model and in about a second.

Each check returns findings in the rulebook shape, with the STD id that owns the rule in orobas/rules/STANDARDS.md.
A Gate 0 Blocker stops the ladder before the container starts. The checks hold for any repository and stay narrow on
purpose: a false Blocker costs more than a miss, because Gate 1 reads the same rules with judgement.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402
from github import ATTRIBUTION  # noqa: E402

from diff import right_rows  # noqa: E402

# The branch prefixes, and the conventional-commit types a title may use. A title type must match its branch prefix.
TYPES = config.items("OROBAS_BRANCH_TYPES", "feat,fix,chore,docs,refactor,test,perf,ci,build")
# true: a title needs a scope, `feat(api): ...`. false: `feat: ...` is fine too.
NEEDS_SCOPE = config.flag("OROBAS_TITLE_NEEDS_SCOPE", False)
# The bases a PR may target besides LEGION_BASE_BRANCH, as a `prototypes` branch; a stacked PR is fine on any open head.
EXTRA_BASES = config.items("OROBAS_EXTRA_BASES")
# Long-lived branches whose merge into a PR branch would ship other people's unreviewed work, as `dev`.
FORBIDDEN_MERGES = config.items("OROBAS_FORBIDDEN_MERGES")
REQUIRE_ISSUE = config.flag("OROBAS_REQUIRE_ISSUE", True)
NO_ISSUE_LABEL = "no-issue"  # a PR with no issue on purpose; see verdict.ALWAYS_BLOCK
TEMPLATE = ".github/PULL_REQUEST_TEMPLATE.md"  # read from the base commit, never from the host copy
EMPTY_STEP = re.compile(r"^\s*[0-9]+\.\s*$", re.MULTILINE)
ENV_NAME = re.compile(r"(^|/)(\.env(\..+)?|[^/]+\.env)$")
ENV_TEMPLATES = (".example", ".sample", ".template")
CONFLICT = re.compile(r"^(<{7}|>{7})( |$)")
# `=======` alone is also a Markdown heading underline, so it counts only beside a real marker in the same patch.
SEPARATOR = re.compile(r"^={7}$")


def title_pattern() -> re.Pattern:
    scope = r"\([^)]+\)" if NEEDS_SCOPE else r"(?:\([^)]+\))?"
    return re.compile(rf"^({'|'.join(map(re.escape, TYPES))}){scope}!?: .+")


def merge_pattern() -> re.Pattern | None:
    if not FORBIDDEN_MERGES:
        return None
    names = "|".join(map(re.escape, FORBIDDEN_MERGES))
    return re.compile(rf"^Merge (?:remote-tracking )?branch '(?:origin/)?(?:{names})'", re.MULTILINE)


def added_lines(patch: str) -> list[tuple[int, str]]:
    """(right-side line number, text) for every added line of one file's patch."""
    return [(number, row[1:]) for number, row in right_rows(patch) if row.startswith("+")]


def comment(rule: str, severity: str, path: str, line: int, body: str) -> dict:
    return {"rule": rule, "severity": severity, "path": path, "line": line, "body": body}


def process(rule: str, severity: str, text: str) -> dict:
    return {"rule": rule, "severity": severity, "text": text}


def template_prompts(template: str) -> set[str]:
    """The one-line `<!-- ... -->` prompts of the PR template: a body that still holds one was never filled in."""
    lines = (line.strip() for line in template.splitlines())
    return {line for line in lines if line.startswith("<!--") and line.endswith("-->")}


def check_meta(meta: dict, open_heads: set[str], prompts: set[str]) -> list[dict]:
    found = []
    body = meta.get("body") or ""
    head, base = meta.get("head") or "", meta.get("base") or ""
    if REQUIRE_ISSUE and not meta.get("linkedIssues") and NO_ISSUE_LABEL not in meta.get("labels", []):
        found.append(process("STD-01", "blocker", "The PR links no issue that GitHub registers. Could we add `Closes #N` "
                                                  f"to the body, or the `{NO_ISSUE_LABEL}` label when it has none on purpose?"))
    title = title_pattern().match(meta.get("title") or "")
    prefix = next((t for t in TYPES if head.startswith(f"{t}/")), "")
    if not title or (prefix and title.group(1) != prefix):
        shape = "`type(scope): message`" if NEEDS_SCOPE else "`type: message`"
        found.append(process("STD-02", "important", f"The title does not follow {shape} with the type of the branch prefix."))
    if not prefix:
        names = ", ".join(f"`{t}/`" for t in TYPES)
        found.append(process("STD-02", "note", f"Nit: the branch `{head}` has none of the prefixes {names}."))
    stacked = base in open_heads and "stacked on #" in body.lower()
    if base not in (config.BASE_BRANCH, *EXTRA_BASES) and not stacked:
        found.append(process("STD-03", "blocker", f"The base is `{base}`, and this branch goes to `{config.BASE_BRANCH}`."))
    if meta.get("mergeable") is False:
        found.append(process("STD-03", "blocker", "The branch has conflicts with its base. Could we merge the base in?"))
    merges = merge_pattern()
    if merges and any(merges.search(c.get("message") or "") for c in meta.get("commits", [])):
        found.append(process("STD-03", "blocker", "A commit merges a long-lived branch into this one, so this PR would ship "
                                                  "other people's unreviewed work."))
    if any(line.strip() in prompts for line in body.splitlines()) or (prompts and EMPTY_STEP.search(body)):
        found.append(process("STD-04", "important", "The body still holds a prompt or an empty step from the template."))
    if config.flag("OROBAS_FORBID_ATTRIBUTION", True) and \
            any(ATTRIBUTION.search(text) for text in [body] + [c.get("message") or "" for c in meta.get("commits", [])]):
        found.append(process("STD-05", "important", "The body or a commit carries an AI attribution line. Could we drop it?"))
    return found


def check_file(f: dict) -> list[dict]:
    """The line rules for one file."""
    path, status = f.get("filename", ""), f.get("status", "")
    name = path.rsplit("/", 1)[-1]
    found = []
    if status == "added" and ENV_NAME.search(path) and not name.endswith(ENV_TEMPLATES):
        found.append(comment("STD-06", "blocker", path, 1, "I think this commits an env file. A credential never enters "
                                                           "git: could it live in the secret store instead?"))
    added = added_lines(f.get("patch") or "")
    conflicted = any(CONFLICT.match(text) for _, text in added)
    for line, text in added:
        if CONFLICT.match(text) or (conflicted and SEPARATOR.match(text)):
            found.append(comment("STD-07", "blocker", path, line, "This looks like a merge conflict marker that was left in."))
    return found


def check(meta: dict, files: list[dict], open_heads: set[str], prompts: set[str] | None = None) -> dict:
    """The Gate 0 result: comments on lines, process findings, and the Blocker count."""
    found = check_meta(meta, open_heads, prompts or set())
    for f in files:
        found += check_file(f)
    comments = [x for x in found if "path" in x]
    procs = [x for x in found if "path" not in x]
    return {"gate": "gate0", "comments": comments, "process": procs,
            "blockers": sum(x["severity"] == "blocker" for x in found)}
