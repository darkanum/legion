"""The one table of Orobas's review ladder: each stage's skill, model, effort, turns and timeout, and the lane allowlist.

Change a model or a limit here, by PR, or the models in legion.env. The host writes this table to /in/stages.json;
review.sh reads only that file, so the PR under review cannot change its own stages. The rules live in the rulebook
(orobas/rules/, or the repository's own LEGION_RULES_DIR).

Each stage has a daemon of the Goetia whose office fits the work, as Orobas himself answers truly about what was done.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "lib"))
import config  # noqa: E402

DEEP = config.MODEL
FAST = config.FAST_MODEL

GATES = {
    # Furcas teaches logic and rhetoric: the standards, read with judgement.
    "gate1": {"persona": "📏 Furcas", "title": "Standards", "skill": "OROBAS_STANDARDS", "command": "/orobas-standards",
              "model": DEEP, "effort": "medium", "turns": 40, "timeout": 300, "out": "gate1.json"},
    # Bathin carries people from one country to another: the triage sends the PR to its lanes.
    "gate2": {"persona": "🧭 Bathin", "title": "Triage", "skill": "OROBAS_TRIAGE", "command": "/orobas-triage",
              "model": FAST, "effort": "low", "turns": 8, "timeout": 120, "out": "gate2.json"},
    # Dantalion knows the thoughts of every person: the intent behind the change.
    "gate3": {"persona": "🎭 Dantalion", "title": "Intent", "skill": "OROBAS_INTENT", "command": "/orobas-intent",
              "model": DEEP, "effort": "medium", "turns": 45, "timeout": 360, "out": "gate3.json"},
    # Orobas answers truly of things past, present and to come, and never deceives: he writes the verdict.
    "report": {"persona": "🐴 Orobas", "title": "Report", "skill": "OROBAS_REPORT", "command": "/orobas-report",
               "model": FAST, "effort": "low", "turns": 6, "timeout": 120, "out": "summary.md"},
}
# After the first full review, a push gets only this: one stage that reads the diff since the last check and says
# which of the bot's open threads it fixes. A review request from a person runs the full ladder.
RECHECK = {"persona": "🐴 Orobas", "title": "Recheck", "skill": "OROBAS_RECHECK", "command": "/orobas-recheck",
           "model": FAST, "effort": "low", "turns": 15, "timeout": 240, "out": "recheck.json"}
LANE = {"skill": "OROBAS_LANE", "command": "/orobas-lane", "model": DEEP, "effort": "medium", "turns": 30, "timeout": 300}
MAX_LANES = int(config.number("OROBAS_MAX_LANES", 4))
# Gate 2 at most, then the lanes and Gate 3 in parallel, then the report, plus the clone.
WALL_SECONDS = 720

# Ordered by risk: when Bathin picks more than MAX_LANES, the first ones in this order run.
LANES = {
    # Eligos discovers hidden things.
    "security": {"persona": "🛡️ Eligos", "color": "b60205", "description": "Orobas lane: who can reach what, and what leaves"},
    # Zagan turns one substance into another, wine into water and metal into coin.
    "data": {"persona": "🗄️ Zagan", "color": "5319e7", "description": "Orobas lane: schemas, migrations, queries and indexes"},
    # Paimon teaches every art and science and the secrets of the mind.
    "ai": {"persona": "🧠 Paimon", "color": "1d76db", "description": "Orobas lane: model calls, prompts, evals and cost"},
    # Amon tells of things past and to come, and reconciles friends.
    "product": {"persona": "🎯 Amon", "color": "d93f0b", "description": "Orobas lane: what was asked, and what the user gets"},
    # Seere goes to any place on earth in an instant.
    "performance": {"persona": "⚡ Seere", "color": "fbca04", "description": "Orobas lane: hot paths, memory, reads and queues"},
    # Halphas builds towers, fills them with weapons and sends warriors where they must go.
    "devops": {"persona": "🏗️ Halphas", "color": "0e8a16", "description": "Orobas lane: CI, images, health, logs and monitors"},
    # Sabnock builds high towers and fortifies castles.
    "infra": {"persona": "🏰 Sabnock", "color": "006b75", "description": "Orobas lane: infrastructure as code, names and cost"},
    # Andrealphus teaches geometry and all that belongs to measurement.
    "design": {"persona": "📐 Andrealphus", "color": "e99695", "description": "Orobas lane: the build matches the design and the design system"},
    # Foras teaches logic and ethics and finds what was lost.
    "tests": {"persona": "🔬 Foras", "color": "c5def5", "description": "Orobas lane: the behaviour is proven"},
}
LABEL_PREFIX = "review:"
SKIP_LABEL = "legion:skip"
LABELS = {
    SKIP_LABEL: {"color": "ededed", "description": "No Legion agent reviews or fixes this"},
    **{f"{LABEL_PREFIX}{lane}": {"color": spec["color"], "description": spec["description"]} for lane, spec in LANES.items()},
}


def valid_lanes(gate2: dict) -> list[str]:
    """The lanes Bathin picked, kept only when allowed, without duplicates, in risk order, cut at MAX_LANES."""
    picked = {lane for lane in (gate2.get("lanes") or []) if isinstance(lane, str)}
    return [lane for lane in LANES if lane in picked][:MAX_LANES]


def skills() -> list[str]:
    return [gate["skill"] for gate in GATES.values()] + [LANE["skill"], RECHECK["skill"]]


def stages_json() -> dict:
    """What the host writes to /in/stages.json for review.sh."""
    return {"gates": GATES, "lane": LANE, "recheck": RECHECK, "lanes": list(LANES), "maxLanes": MAX_LANES,
            "wallSeconds": WALL_SECONDS, "skills": skills()}
