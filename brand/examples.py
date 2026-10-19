"""Renders the example interactions in the README: a Marbas thread in Slack, an Orobas report on a PR, a Valac issue.

Usage (from the repository root):  python brand/examples.py
Needs Chrome or Edge (as brand/build.py) and `gh` signed in: GitHub's own Markdown API renders the PR and issue text,
so it looks as it does on github.com.

Every word in the images comes from the host code itself. Marbas's thread is a replay of listener.py with Slack and
GitHub stubbed, the Orobas report is report.render(), and the Valac issue is issue_text.title() and body(). When a
message format changes, run this again and the images follow. The scenario (acme/shop, the people, the bug) is made
up; the formats are not.
"""

import html
import json
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
OUT = ROOT / "examples"
TMP = Path(tempfile.mkdtemp(prefix="legion-examples-"))
sys.path[:0] = [str(REPO / "shared" / "lib")]

import build  # noqa: E402  the brand constants and the screenshot helper

SCENE_REPO = "acme/shop"


def gfm(markdown: str) -> str:
    """GitHub-flavoured Markdown to HTML, by GitHub itself."""
    done = subprocess.run(["gh", "api", "markdown", "-f", f"text={markdown}", "-f", "mode=gfm", "-f",
                           f"context={SCENE_REPO}"], capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout


# ---------------------------------------------------------------- Marbas: a replay of listener.py


def marbas_thread() -> list[dict]:
    """The messages of one bug thread, in order: who, the text, and the buttons if any."""
    for name in ("slack_sdk", "slack_sdk.socket_mode", "slack_sdk.socket_mode.request", "slack_sdk.socket_mode.response"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["slack_sdk"].WebClient = mock.MagicMock
    for name, attr in (("slack_sdk.socket_mode", "SocketModeClient"), ("slack_sdk.socket_mode.request", "SocketModeRequest"),
                       ("slack_sdk.socket_mode.response", "SocketModeResponse")):
        setattr(sys.modules[name], attr, object)
    env = {"LEGION_REPO": SCENE_REPO, "MARBAS_CHANNELS": "C1", "LEGION_ONCALL_MENTION": ""}
    sys.path[:0] = [str(REPO / "marbas" / "host")]
    with mock.patch.dict("os.environ", env), mock.patch.object(Path, "read_text", return_value="token"):
        import listener  # noqa: E402
    import state  # noqa: E402

    posted: list[dict] = []
    web = listener.web = mock.MagicMock()

    def post(channel, text, thread_ts=None, blocks=None):
        buttons = [e["text"]["text"] for b in (blocks or []) if b["type"] == "actions" for e in b["elements"]]
        posted.append({"who": "Marbas", "text": text, "buttons": buttons})
        return {"ts": f"1.{len(posted)}"}

    def update(channel, ts, text, blocks=None):
        # A decided plan is edited in place: show the edited version where the plan was.
        for message in posted:
            if message["buttons"]:
                message.update(text=text, buttons=[])

    web.chat_postMessage.side_effect = post
    web.chat_update.side_effect = update
    listener.BOT_ID = "B0"
    link = f"<https://github.com/{SCENE_REPO}/issues/412|#412>"
    trigger = {"channel": "C1", "ts": "1.0", "user": "U1", "text": f"Checkout total is off by a cent: {link}"}
    plan = {"outcome": "plan", "score": 86, "label": "High", "plan": {
        "summary": {"found": "Totals with three items or more round each line to cents, then add the rounded lines.",
                    "cause": "`cart/total.ts` sums floats and rounds per line, so 3 × 19.99 with a 15% coupon is 50.97, not 50.98.",
                    "fix": "Keep every amount as integer cents and round once, after the coupon.",
                    "proof": "A failing test for the three-item case, then the whole cart suite and the build."},
        "files": ["cart/total.ts", "cart/total.test.ts (new)"], "packages": ["cart"]}}
    ready = {"outcome": "ready", "score": 86, "label": "High"}
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(state, "STATE", Path(tmp)), mock.patch.object(listener, "STATE", Path(tmp)), \
            mock.patch.object(listener, "github", return_value={"state": "open", "labels": [{"name": "bug"}]}), \
            mock.patch.object(listener, "engineers", return_value={"U1"}), \
            mock.patch.object(listener.pr, "open_prs_for", return_value=[]), \
            mock.patch.object(listener.pr, "open_pr", return_value={"pr": 418, "url": f"https://github.com/{SCENE_REPO}/pull/418"}), \
            mock.patch.object(listener.pr, "extra_files", return_value=[]), \
            mock.patch.object(listener.axiom, "emit"), mock.patch.object(listener, "jobs"), \
            mock.patch.object(listener, "RUNS", Path(tmp) / "runs"):
        (Path(tmp) / "runs" / "412-1").mkdir(parents=True)
        listener.on_message(trigger)
        key = "C1-1.0"
        listener.after_plan(key, listener.load(key), plan)
        thread = listener.load(key)
        with mock.patch.object(listener.time, "time", return_value=1792761300):
            listener.on_action({"actions": [{"action_id": "approve", "value": "412:1"}], "channel": {"id": "C1"},
                                "user": {"id": "U1"}, "message": {"thread_ts": "1.0"}})
        listener.publish(key, listener.load(key), ready)
    return [{"who": "ana", "text": trigger["text"], "buttons": []}] + posted


EMOJI = {"lion_face": "🦁", "heavy_check_mark": "✔️", "white_check_mark": "✅", "x": "❌", "eyes": "👀"}


def slack_html(text: str) -> str:
    """Slack mrkdwn to HTML, enough for what Marbas posts."""
    text = text.replace("⁠", "")
    text = re.sub(r"<!date\^\d+\^[^|]*\|([^>]*)>", lambda m: "Oct 19 at 14:35", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r'<a>\2</a>', text)
    text = re.sub(r"<(https?://[^>]+)>", r'<a>\1</a>', text)
    text = re.sub(r"<@U1>", '<span class="mention">@ana</span>', text)
    text = re.sub(r":([a-z_]+):", lambda m: EMOJI.get(m.group(1), m.group(0)), text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?!\w)", r"<i>\1</i>", text)
    return text.replace("\n", "<br>")


def marbas_page(messages: list[dict]) -> str:
    rows = []
    for m in messages:
        bot = m["who"] == "Marbas"
        avatar = (f'<div class="avatar bot"><svg viewBox="0 0 64 64">{build.mark_body(build.AGENTS[0], "dark")}</svg></div>'
                  if bot else '<div class="avatar">A</div>')
        name = "Marbas <span class=app>APP</span>" if bot else "ana"
        buttons = "".join(f'<span class="btn {"primary" if b == "Approve" else "danger"}">{b}</span>' for b in m["buttons"])
        rows.append(f'<div class="msg">{avatar}<div><div class="name">{name} <span class="time">14:3{len(rows)}</span></div>'
                    f'<div class="text">{slack_html(m["text"])}</div>{f"<div class=buttons>{buttons}</div>" if buttons else ""}</div></div>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#1a1d21;color:#d1d2d3;font:15px/1.46 -apple-system,"Segoe UI",Lato,sans-serif;width:920px}}
.head{{padding:14px 20px;border-bottom:1px solid #35373b;font-weight:700;color:#fff}}
.head span{{color:#ababad;font-weight:400;margin-left:8px}}
.msg{{display:flex;gap:12px;padding:10px 20px}}
.avatar{{width:36px;height:36px;border-radius:6px;background:#5b45d6;color:#fff;font-weight:700;display:grid;place-items:center;flex:none}}
.avatar.bot{{background:{build.INK};padding:3px;box-sizing:border-box}}
.name{{font-weight:900;color:#fff}} .time{{color:#ababad;font-weight:400;font-size:12px;margin-left:6px}}
.app{{background:#35373b;color:#ababad;font-size:10px;padding:1px 4px;border-radius:3px;font-weight:700}}
code{{background:#2c2d30;border:1px solid #3b3c3f;border-radius:3px;padding:1px 3px;color:#e8912d;font-size:13px}}
a{{color:#1d9bd1}} .mention{{background:#1d9bd133;color:#1d9bd1;border-radius:3px;padding:0 2px}}
.buttons{{margin-top:8px;display:flex;gap:8px}}
.btn{{padding:6px 14px;border-radius:4px;font-weight:700;font-size:14px;border:1px solid #565856}}
.btn.primary{{background:#007a5a;border-color:#007a5a;color:#fff}} .btn.danger{{color:#e01e5a;border-color:#e01e5a}}
.note{{padding:10px 20px 16px;color:#ababad;font-size:12px}}
</style></head><body><div class="head"># bugs <span>Thread</span></div>{"".join(rows)}
<div class="note">Illustration: a replay of marbas/host/listener.py with Slack and GitHub stubbed. The scenario is made up.</div>
</body></html>"""


# ---------------------------------------------------------------- Orobas: report.render()


def orobas_report() -> str:
    sys.path[:0] = [str(REPO / "orobas" / "host")]
    with mock.patch.dict("os.environ", {"LEGION_REPO": SCENE_REPO}):
        import report  # noqa: E402
    sha = "9f2c4be71d0a3e5b6c8d9e0f1a2b3c4d5e6f7a8b"

    def found(severity, rule, path, line, body):
        return {"rule": rule, "severity": severity, "path": path, "line": line, "body": body}

    ladder = {
        "gate0": {"comments": [], "process": []},
        "gate1": {"verdict": "pass", "process": [], "comments": [
            found("note", "STD-21", "cart/total.ts", 41, "I think this comment says what the line does; could it say why rounding happens last?")]},
        "gate2": {"lanes": ["data", "tests"]},
        "lanes": {"data": {"comments": [], "process": []},
                  "tests": {"process": [], "comments": [
                      found("blocker", "TEST-01", "cart/total.test.ts", 12,
                            "I think this test passes before the fix too: 2 items never hit the rounding. Could it use 3 items with the 15% coupon?")]}},
        "gate3": {"verdict": "pass", "comments": [], "process": []},
        "status": {"stages": [{"id": "gate1", "status": "pass"}, {"id": "gate2", "status": "pass"},
                              {"id": "lane-data", "status": "pass"}, {"id": "lane-tests", "status": "fail"},
                              {"id": "gate3", "status": "pass"}], "lanes": ["data", "tests"], "stoppedAt": None}}
    summary = ("The PR keeps cart amounts in integer cents and rounds once after the coupon, which is what #412 asks. "
               "One blocker is left: the new test does not reach the case it fixes.")
    usage = {"tokensIn": 182000, "tokensOut": 9400, "costUsd": 1.42, "windowPct": 0.9}
    body = report.render(sha, ladder, "COMMENT", "This has 1 Blocker(s). I do not block PRs for now, so a person decides.",
                         summary, usage)
    return body.split("\n<!-- orobas-report")[0]


# ---------------------------------------------------------------- Valac: issue_text


def valac_issue() -> tuple[str, str]:
    sys.path[:0] = [str(REPO / "valac" / "host")]
    with mock.patch.dict("os.environ", {"LEGION_REPO": SCENE_REPO, "LEGION_ISSUE_TITLE_PREFIX": "[bug] "}):
        import issue_text  # noqa: E402
        finding = {"title": "Coupon validation times out under load", "class": "code-bug", "dataset": "api",
                   "eventType": "coupon_validate_timeout", "score": 78, "signals": ["users>40", "new", "rising"],
                   "cause": "Each checkout validates its coupon with a query that scans `coupons` by code with no index. "
                            "Since Friday's campaign the table grew tenfold, and the query passes the 2 s budget at peak.",
                   "location": {"file": "api/coupons/validate.ts", "line": 58},
                   "proposedFix": "Add a unique index on `coupons.code` in a migration, and look the coupon up by it.",
                   "humanOnly": False,
                   "evidence": {"apl": "['api'] | where eventType == 'coupon_validate_timeout' | summarize rows = count(), users = dcount(userId) by bin(_time, 1h)",
                                "rows": 1287, "users": 46, "windowStart": "2026-10-18T07:00Z", "windowEnd": "2026-10-19T07:00Z"}}
        return issue_text.title(finding), issue_text.body(finding)


def github_page(kind: str, title: str, meta: str, body_html: str, author: str, avatar: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,"Segoe UI","Noto Sans",sans-serif;width:980px}}
.wrap{{padding:24px 28px}}
h1{{font-weight:400;font-size:26px;margin:0}} h1 span{{color:#8d96a0}}
.meta{{margin:8px 0 20px;color:#8d96a0}} .state{{background:#238636;color:#fff;border-radius:2em;padding:4px 12px;font-weight:600;margin-right:8px}}
.comment{{border:1px solid #30363d;border-radius:6px}}
.chead{{background:#161b22;border-bottom:1px solid #30363d;padding:8px 16px;color:#8d96a0;display:flex;align-items:center;gap:8px}}
.chead b{{color:#e6edf3}} .bot{{border:1px solid #30363d;border-radius:2em;font-size:12px;padding:0 6px}}
.av{{width:24px;height:24px;border-radius:50%;background:{build.INK};padding:2px;box-sizing:border-box}}
.md{{padding:16px}} .md table{{border-collapse:collapse;margin:12px 0}} .md th,.md td{{border:1px solid #30363d;padding:6px 13px}}
.md tr:nth-child(2n){{background:#151b23}} .md code{{background:#656c7633;padding:.2em .4em;border-radius:6px;font-size:85%}}
.md pre{{background:#151b23;padding:16px;border-radius:6px;overflow:hidden;white-space:pre-wrap}} .md pre code{{background:none;padding:0}}
.md a{{color:#4493f8;text-decoration:none}} .md h2{{border-bottom:1px solid #30363d;padding-bottom:.3em;font-size:1.5em;margin-top:16px}}
.md h3{{font-size:1.25em}} .md sub{{color:#8d96a0}}
.note{{color:#8d96a0;font-size:12px;margin-top:12px}}
</style></head><body><div class="wrap"><h1>{html.escape(title)}</h1>
<div class="meta"><span class="state">Open</span>{meta}</div>
<div class="comment"><div class="chead"><span class="av"><svg viewBox="0 0 64 64">{avatar}</svg></span><b>{author}</b><span class="bot">bot</span> {kind}</div>
<div class="md">{body_html}</div></div>
<div class="note">Illustration: rendered from the Legion host code by brand/examples.py. The scenario is made up.</div></div></body></html>"""


def shoot(page: Path, png: Path, width: int, height: int) -> None:
    """Screenshots the page tall enough, then cuts the empty background below the content, 16 px kept."""
    build.shoot(page, png, width, height)
    try:
        from PIL import Image  # optional: without Pillow the image keeps its empty bottom
    except ImportError:
        return
    image = Image.open(png).convert("RGB")
    background = image.getpixel((width - 1, height - 1))
    bottom = next((y for y in range(height - 1, 0, -1)
                   if any(image.getpixel((x, y)) != background for x in range(0, width, 4))), height - 1)
    image.crop((0, 0, width, min(height, bottom + 16))).save(png, optimize=True)


def main():
    OUT.mkdir(exist_ok=True)
    page = TMP / "marbas.html"
    page.write_text(marbas_page(marbas_thread()), encoding="utf-8")
    shoot(page, OUT / "marbas-thread.png", 920, 1400)

    orobas, valac = build.AGENTS[2], build.AGENTS[1]
    page = TMP / "orobas.html"
    page.write_text(github_page("commented on this pull request", "fix: keep cart amounts in integer cents #418",
                                "legion-bot wants to merge 2 commits into <code>main</code> from <code>fix/issue-412-checkout-total</code>",
                                gfm(orobas_report()), "legion", build.mark_body(orobas, "dark")), encoding="utf-8")
    shoot(page, OUT / "orobas-report.png", 980, 1400)

    title, body = valac_issue()
    page = TMP / "valac.html"
    page.write_text(github_page("opened this issue", f"{title} #421", "legion opened this issue · assigned to ana · label <code>bug</code>",
                                gfm(body), "legion", build.mark_body(valac, "dark")), encoding="utf-8")
    shoot(page, OUT / "valac-issue.png", 980, 1400)
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
