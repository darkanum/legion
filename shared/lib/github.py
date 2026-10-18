"""The GitHub calls and rules that more than one agent host uses: Marbas (pr.py), Valac and Orobas (reviewer.py).

The host token can write, and on a protected base branch it may hold a bypass, so it never enters a sandbox.
"""

import base64
import json
import re
import subprocess
import urllib.error
import urllib.request
from typing import Iterator

import config
from paths import SECRETS

REPO = config.REPO
# No agent change may touch these paths: Marbas and Valac refuse the patch, Orobas never approves the PR.
GUARDED = config.GUARDED
ENV_FILE = re.compile(r"(^|/)\.env($|\.)")
# A line that credits an AI tool. Narrow, so "generated with protoc" or a human co-author never matches.
AI_TOOLS = r"(?:claude|anthropic|chatgpt|openai|copilot|cursor|codex|gemini|an? ai\b|ai\b)"
ATTRIBUTION = re.compile(rf"(?:generated|written|created|authored) (?:with|by|using) \[?{AI_TOOLS}"
                         rf"|co-authored-by:[^\n]*{AI_TOOLS}", re.IGNORECASE)
NEXT_PAGE = re.compile(r'<([^>]+)>;\s*rel="next"')
API = "https://api.github.com/"


def github(path: str, token_file: str = "github_host_token", method: str = "GET", body: dict | None = None) -> object:
    return call(API + path, token_file, method, body)[0]


def pages(path: str, token_file: str = "github_host_token") -> Iterator[list]:
    """Each page of a list, following the Link header: the only way through a list that GitHub pages by cursor."""
    url: str | None = API + path
    while url:
        data, link = call(url, token_file)
        yield data
        found = NEXT_PAGE.search(link)
        url = found.group(1) if found else None


def call(url: str, token_file: str = "github_host_token", method: str = "GET", body: dict | None = None) -> tuple[object, str]:
    """(the JSON answer, the Link header)."""
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {(SECRETS / token_file).read_text().strip()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": config.USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw, link = response.read(), response.headers.get("Link") or ""
    return (json.loads(raw) if raw else {}), link


def git_auth(token_file: str = "github_host_token") -> str:
    """A `git -c` value that sends the token as a header: not in the URL, so a git error never prints it."""
    token = (SECRETS / token_file).read_text().strip()
    return f"http.extraHeader=Authorization: Basic {base64.b64encode(f'x-access-token:{token}'.encode()).decode()}"


def team_logins() -> set[str]:
    """The engineers: the members of LEGION_TEAM (`org/slug`), or every collaborator with push access when it is empty."""
    if config.TEAM:
        org, _, slug = config.TEAM.partition("/")
        return {m["login"] for page in pages(f"orgs/{org}/teams/{slug}/members?per_page=100") for m in page}
    return {c["login"] for page in pages(f"repos/{REPO}/collaborators?permission=push&per_page=100") for c in page
            if c.get("type") != "Bot"}


def team_mention() -> str:
    """Who a person-only step names on GitHub: the team, or the repository owner when there is no team."""
    return f"@{config.TEAM}" if config.TEAM else f"@{config.repo_parts()[0]}"


def guarded_paths(paths) -> list[str]:
    return [p for p in paths if p.startswith(GUARDED) or ENV_FILE.search(p)]


def checks_state(sha: str) -> str:
    """"green", "red" or "pending" for every check and status on a commit.

    GraphQL, because a fine-grained token has no Checks permission and the REST check-runs call answers 403.
    """
    owner, name = REPO.split("/")
    query = ('query($owner: String!, $name: String!, $sha: String!) { repository(owner: $owner, name: $name) '
             '{ object(expression: $sha) { ... on Commit { statusCheckRollup { state } } } } }')
    data = github("graphql", method="POST", body={"query": query, "variables": {"owner": owner, "name": name, "sha": sha}})
    rollup = ((data.get("data") or {}).get("repository") or {}).get("object", {}).get("statusCheckRollup")
    state = (rollup or {}).get("state")
    if state == "SUCCESS":
        return "green"
    if state in ("FAILURE", "ERROR"):
        return "red"
    return "pending"  # PENDING, EXPECTED, or no check yet


def describe(error: Exception) -> str:
    """A one-line cause for a message, with no command output: git or GitHub output can hold a path or a secret."""
    if isinstance(error, urllib.error.HTTPError):
        path = re.sub(r"[0-9a-f]{40}", "<sha>", error.url.split("api.github.com/")[-1].split("?")[0])
        return f"GitHub answered *{error.code}* on `{path}`"
    if isinstance(error, subprocess.CalledProcessError):
        return "a git command failed (for example, the patch does not apply)"
    if getattr(error, "public", False):
        return str(error)  # an error written by the host for people, as pull_request.ScopeRefused
    return type(error).__name__
