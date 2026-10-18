"""Run with: python3 -m unittest test_stages (no dependencies)."""

import re
import unittest
from pathlib import Path

import stages

ORO = Path(__file__).resolve().parents[1]
RULES = ORO / "rules"
SKILLS = ORO / "skills"


class LanesTest(unittest.TestCase):
    def test_an_unknown_lane_is_dropped_and_the_order_is_by_risk(self):
        self.assertEqual(stages.valid_lanes({"lanes": ["tests", "evil", "security", "security", 3]}), ["security", "tests"])

    def test_the_lanes_are_cut_at_the_cap(self):
        self.assertEqual(len(stages.valid_lanes({"lanes": list(stages.LANES)})), stages.MAX_LANES)

    def test_no_lanes_is_empty(self):
        self.assertEqual(stages.valid_lanes({}), [])

    def test_lane_ids_are_safe_for_a_label_and_a_file_name(self):
        for lane in stages.LANES:
            self.assertRegex(lane, r"^[a-z]+$")

    def test_every_lane_and_the_skip_label_have_a_label(self):
        self.assertIn(stages.SKIP_LABEL, stages.LABELS)
        for lane in stages.LANES:
            self.assertRegex(stages.LABELS[f"review:{lane}"]["color"], r"^[0-9a-f]{6}$")


class TableTest(unittest.TestCase):
    def test_every_stage_has_its_limits(self):
        for gate in list(stages.GATES.values()) + [stages.LANE]:
            for key in ("skill", "command", "model", "effort", "turns", "timeout"):
                self.assertIn(key, gate)

    def test_the_ladder_fits_the_wall_clock(self):
        g = stages.GATES
        parallel = max(g["gate3"]["timeout"], stages.LANE["timeout"], g["gate1"]["timeout"] - g["gate2"]["timeout"])
        self.assertLessEqual(g["gate2"]["timeout"] + parallel + g["report"]["timeout"], stages.WALL_SECONDS)

    def test_the_json_lists_every_skill(self):
        data = stages.stages_json()
        self.assertEqual(sorted(data["skills"]), sorted({g["skill"] for g in stages.GATES.values()} | {"OROBAS_LANE", "OROBAS_RECHECK"}))
        self.assertEqual(data["lanes"], list(stages.LANES))


class DriftTest(unittest.TestCase):
    def test_the_starter_rulebook_has_every_gate_and_lane_file(self):
        for name in ("README.md", "STANDARDS.md", "TRIAGE.md", "INTENT.md", "VOICE.md"):
            self.assertTrue((RULES / name).exists(), name)
        for lane in stages.LANES:
            self.assertTrue((RULES / f"LANE_{lane.upper()}.md").exists(), lane)

    def test_every_gate0_rule_is_in_the_standards(self):
        standards = (RULES / "STANDARDS.md").read_text(encoding="utf-8")
        for rule in [f"STD-0{n}" for n in range(1, 8)]:
            self.assertRegex(standards, rf"(?m)^#+ {rule}\b", rule)

    def test_every_skill_exists_and_triage_names_every_lane(self):
        for skill in stages.skills():
            self.assertTrue((SKILLS / skill / "SKILL.md").exists(), skill)
        triage = (SKILLS / "OROBAS_TRIAGE" / "SKILL.md").read_text(encoding="utf-8")
        for lane in stages.LANES:
            self.assertRegex(triage, rf"\b{re.escape(lane)}\b")


class SandboxTest(unittest.TestCase):
    def test_every_stage_runs_with_subagents_off(self):
        script = (Path(__file__).resolve().parents[1] / "sandbox" / "review.sh").read_text(encoding="utf-8")
        calls = re.findall(r"local flags=\(([^)]*)\)", script)
        self.assertEqual(len(calls), 1, "one place builds the claude flags")
        self.assertIn("--disallowedTools Agent Task", calls[0])
        # One claude call, and it takes those flags: a second call or a lost "${flags[@]}" would skip the guard.
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(code.count("claude -p"), 1)
        self.assertRegex(code, r'claude -p "\$PREAMBLE[^"]*"\s*"\$\{flags\[@\]\}"')


if __name__ == "__main__":
    unittest.main()
