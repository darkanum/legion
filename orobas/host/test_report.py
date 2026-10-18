"""Run with: python3 -m unittest test_report (no dependencies)."""

import unittest

import report

SHA = "c" * 40


def finding(severity, rule="SEC-01", path="a.ts", line=3, body="I think this route takes any role."):
    return {"rule": rule, "severity": severity, "path": path, "line": line, "body": body}


def full_ladder():
    return {"gate0": {"comments": [], "process": []},
            "gate1": {"verdict": "pass", "comments": [finding("note", "STD-25")], "process": []},
            "gate2": {"lanes": ["security", "tests"]},
            "lanes": {"security": {"comments": [finding("blocker")], "process": []}},
            "gate3": {"verdict": "pass", "comments": [], "process": [], "summary": "ok"},
            "status": {"stages": [{"id": "gate1", "status": "pass"}, {"id": "gate2", "status": "pass"},
                                  {"id": "lane-security", "status": "pass"},
                                  {"id": "lane-tests", "status": "timeout"}, {"id": "gate3", "status": "pass"}],
                       "lanes": ["security", "tests"], "stoppedAt": None}}


class RenderTest(unittest.TestCase):
    def test_a_full_ladder(self):
        body = report.render(SHA, full_ladder(), "COMMENT", "This has 1 Blocker(s).", "Nice work @someone.")
        self.assertIn("## 🐴 Orobas's report for `ccccccc`", body)
        self.assertIn("| 1 · Standards | 📏 Furcas | 🟡 1 note |", body)
        self.assertIn("| 2 · Triage | 🧭 Bathin | ✅ security, tests |", body)
        self.assertIn("| Lane · security | 🛡️ Eligos | ❌ 1 blocker |", body)
        self.assertIn("| Lane · tests | 🔬 Foras | ⚠️ did not finish (timeout) |", body)
        self.assertIn(f"(https://github.com/owner/repo/blob/{SHA}/a.ts#L3)", body)
        self.assertIn("**Verdict:** COMMENT. This has 1 Blocker(s).", body)
        self.assertIn("@​someone", body)
        self.assertNotIn("Lane · design", body)

    def test_a_stopped_ladder(self):
        ladder = {"gate0": {"comments": [], "process": [{"rule": "STD-01", "severity": "blocker", "text": "No issue."}]},
                  "lanes": {}, "status": {"stoppedAt": "Gate 0", "stages": []}}
        body = report.render(SHA, ladder, "COMMENT", "")
        self.assertIn("| 0 · Rules | 🤖 Host | ❌ 1 blocker |", body)
        self.assertIn("| 3 · Intent | 🎭 Dantalion | ⏭️ did not run |", body)
        self.assertIn("- 🤖 `STD-01`: No issue.", body)
        self.assertIn("The ladder stopped at Gate 0", body)

    def test_the_marker_round_trips_and_a_model_cannot_forge_one(self):
        ladder = full_ladder()
        ladder["gate3"]["comments"] = [finding("blocker", "INT-01", body=report.MARKER + "e30= -->")]
        body = report.render(SHA, ladder, "COMMENT", "")
        self.assertEqual(body.count(report.MARKER), 1)
        meta = report.decode(body)
        self.assertEqual(meta["sha"], SHA)
        self.assertEqual(meta["counts"]["lane-security"]["blocker"], 1)
        self.assertIsNone(report.decode("no marker"))

    def test_find_report_needs_the_marker_and_the_bot(self):
        comments = [{"id": 1, "user": {"login": "eng"}, "body": report.MARKER + "x -->"},
                    {"id": 2, "user": {"login": "legion-bot"}, "body": "hi"},
                    {"id": 3, "user": {"login": "legion-bot"}, "body": "r\n" + report.MARKER + "x -->"}]
        self.assertEqual(report.find_report(comments, "legion-bot")["id"], 3)



class CostLineTest(unittest.TestCase):
    USAGE = {"tokensIn": 380_400, "tokensOut": 31_900, "costUsd": 3.1, "windowPct": 1.9}

    def test_the_report_states_tokens_cost_and_seat_share_in_english(self):
        body = report.render(SHA, full_ladder(), "COMMENT", "", "", self.USAGE)
        self.assertIn("🧾 This review: 412k tokens (380k in · 32k out) · 3.10 USD · 1.9% of the seat window", body)

    def test_no_run_means_no_cost_line(self):
        self.assertNotIn("🧾", report.render(SHA, full_ladder(), "COMMENT", ""))


if __name__ == "__main__":
    unittest.main()
