"""Run with: python3 -m unittest test_valac (no dependencies)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import valac  # first: it puts agents/shared/lib on sys.path

import issue_text  # noqa: E402
import ledger  # noqa: E402

NOW = 1_800_000_000.0


def finding(fingerprint="aaaa1111", score=75, **extra):
    return {"fingerprint": fingerprint, "class": "code-bug", "dataset": "workers",
            "eventType": "checkout_total_failed", "score": score,
            "evidence": {"apl": "['workers'] | summarize count()", "rows": 493, "users": 105},
            "signals": ["users>10", "new"], "location": {"file": "services/api/src/a.ts", "line": 13},
            "cause": "Database transactions expire and nothing retries them.", "proposedFix": "Retry them.",
            "humanOnly": False, **extra}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_new_finding_over_the_floor_is_chosen_best_first(self):
        chosen = ledger.select([finding("aaaa1111", 50), finding("bbbb2222", 90), finding("cccc3333", 30)], self.store, NOW)
        self.assertEqual([f["fingerprint"] for f in chosen], ["bbbb2222", "aaaa1111"])

    def test_a_known_open_finding_opens_nothing(self):
        ledger.opened(self.store, finding(), 4800, NOW)
        self.assertEqual(ledger.select([finding()], self.store, NOW), [])

    def test_the_cap_counts_what_is_already_open(self):
        for i, fp in enumerate(("11111111", "22222222")):
            ledger.opened(self.store, finding(fp), 4800 + i, NOW)
        chosen = ledger.select([finding("33333333"), finding("44444444")], self.store, NOW)
        self.assertEqual(len(chosen), ledger.MAX_ACTIVE - 2)

    def test_a_reject_sleeps_30_days_then_comes_back(self):
        record = ledger.opened(self.store, finding(), 4800, NOW)
        ledger.closed(self.store, record, merged=False, reason="closed as not_planned", now=NOW)
        self.assertEqual(ledger.select([finding()], self.store, NOW + 29 * 86400), [])
        self.assertEqual(len(ledger.select([finding()], self.store, NOW + 31 * 86400)), 1)

    def test_a_merge_never_reopens_the_finding(self):
        record = ledger.opened(self.store, finding(), 4800, NOW)
        ledger.closed(self.store, record, merged=True, reason="merged", now=NOW)
        self.assertEqual(ledger.select([finding()], self.store, NOW + 90 * 86400), [])

    def test_a_fingerprint_that_is_not_hex_is_skipped(self):
        self.assertEqual(ledger.select([finding("../../etc")], self.store, NOW), [])

    def test_a_number_finds_its_finding_by_issue_or_pr(self):
        record = ledger.opened(self.store, finding(), 4800, NOW)
        record["pr"] = 4801
        ledger.save(self.store, record)
        self.assertEqual(ledger.by_number(self.store, 4801)["issue"], 4800)


class IssueTextTest(unittest.TestCase):
    def test_the_body_holds_the_evidence_and_the_apl(self):
        body = issue_text.body(finding())
        self.assertIn("| `workers` | `checkout_total_failed` | 493 | 105 |", body)
        self.assertIn("```kusto\n['workers'] | summarize count()\n```", body)
        self.assertIn("`services/api/src/a.ts:13`", body)
        self.assertIn("Found by Valac. Score: 75.", body)

    def test_an_email_anywhere_refuses_the_finding(self):
        with self.assertRaises(issue_text.Unsafe):
            issue_text.body(finding(cause="failed for a.buyer@shop.example"))

    def test_a_finding_without_counts_is_refused(self):
        with self.assertRaises(issue_text.Unsafe):
            issue_text.body(finding(evidence={"apl": "x"}))

    def test_a_mention_cannot_ping(self):
        self.assertNotIn("@team", issue_text.body(finding(cause="ask @team")))

    def test_human_only_proposes_no_fix(self):
        self.assertIn("a person decides the fix", issue_text.body(finding(humanOnly=True)))

    def test_the_title_is_a_bug_title(self):
        self.assertTrue(issue_text.title(finding()).startswith("[bug] Database transactions expire"))

    def test_the_patrol_title_wins_over_the_cause(self):
        self.assertEqual(issue_text.title(finding(title="Score projection fails")), "[bug] Score projection fails")

    def test_a_long_cause_is_cut_at_a_word(self):
        cause = "Cache reads miss the 200 ms budget on 0.2% of reads, so the breaker opens 30 to 46 times a day against 1 to 5"
        cut = issue_text.title(finding(cause=cause)).removeprefix("[bug] ")
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(len(cut), issue_text.TITLE_MAX + 1)
        self.assertTrue(cause.startswith(cut.removesuffix("…")))
        self.assertEqual(cause[len(cut) - 1], " ")


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)
        self.record = ledger.opened(self.store, finding(), 4800, NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, event, answer):
        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac.inbox, "drain", return_value=[event]), \
             mock.patch.object(valac, "github", return_value=answer), mock.patch.object(valac.axiom, "emit"):
            valac.apply_inbox()
        return ledger.load(self.store, "aaaa1111")

    def test_an_issue_closed_as_not_planned_snoozes_the_finding(self):
        record = self.apply({"event": "issues", "action": "closed", "number": 4800}, {"state_reason": "not_planned"})
        self.assertEqual(record["state"], "snoozed")
        self.assertIn("not_planned", record["rejectReason"])

    def test_an_issue_a_person_closed_as_completed_moves_the_finding_on(self):
        record = self.apply({"event": "issues", "action": "closed", "number": 4800}, {"state_reason": "completed"})
        self.assertEqual(record["state"], "deployed")

    def test_a_merged_pr_moves_the_finding_on(self):
        self.record["pr"] = 4801
        self.record["state"] = "pr_open"
        ledger.save(self.store, self.record)
        record = self.apply({"event": "pull_request", "action": "closed", "number": 4801}, {"merged": True})
        self.assertEqual(record["state"], "deployed")

    def test_an_event_for_another_number_changes_nothing(self):
        record = self.apply({"event": "issues", "action": "closed", "number": 1}, {"state_reason": "not_planned"})
        self.assertEqual(record["state"], "issue_open")

    def test_a_failing_event_is_logged_and_the_next_one_still_moves_its_finding(self):
        second = ledger.opened(self.store, finding("bbbb2222"), 4802, NOW)
        events = [{"event": "issues", "action": "closed", "number": 4800, "delivery": "d1"},
                  {"event": "issues", "action": "closed", "number": 4802, "delivery": "d2"}]

        def github(path, *args, **kwargs):
            if path.endswith("/4800"):
                raise OSError("GitHub is down")
            return {"state_reason": "not_planned"}

        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac.inbox, "drain", return_value=events), \
             mock.patch.object(valac, "github", side_effect=github), mock.patch.object(valac.axiom, "emit") as emit:
            valac.apply_inbox()
        self.assertEqual(ledger.load(self.store, second["fingerprint"])["state"], "snoozed")
        self.assertEqual(ledger.load(self.store, "aaaa1111")["state"], "issue_open")
        self.assertIn("valac.event.failed", [c.args[1] for c in emit.call_args_list])


class PatrolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def patrol(self, findings, can_open_pr=False, open_prs=()):
        report = {"window": {"start": "2026-10-07", "end": "2026-10-14"}, "findings": findings}
        calls = []

        def github(path, *args, method="GET", body=None, **kwargs):
            calls.append((path, method, body))
            if path.startswith("search/issues"):
                return {"items": [{"number": n} for n in open_prs]}
            return {"number": 4800} if method == "POST" else [{"name": "bug"}, {"name": "services/api"}]

        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac, "apply_inbox"), \
             mock.patch.object(valac, "run", return_value=report), mock.patch.object(valac, "github", side_effect=github), \
             mock.patch.object(valac, "CAN_OPEN_PR", can_open_pr), mock.patch.object(valac, "fix") as fix, \
             mock.patch.object(valac, "owner", return_value="ana"), mock.patch.object(valac.axiom, "emit"), \
             mock.patch.object(valac, "say") as self.say, mock.patch.object(valac.config, "PACKAGE_ROOTS", ("services",)):
            valac.patrol()
        return calls, fix

    def test_with_every_place_taken_the_patrol_does_not_run(self):
        for number, fingerprint in enumerate(("aaaa0001", "aaaa0002", "aaaa0003")):
            ledger.opened(self.store, finding(fingerprint), 4900 + number, NOW)
        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac, "apply_inbox"), \
             mock.patch.object(valac, "run") as run, mock.patch.object(valac.axiom, "emit") as emit, \
             mock.patch.object(valac, "say") as say:
            valac.patrol()
        run.assert_not_called()
        self.assertEqual(emit.call_args.args[1], "valac.run.skipped")
        self.assertIn("#4900", say.call_args.args[0])
        self.assertIn("#4902", say.call_args.args[0])

    def test_a_new_finding_opens_one_bug_issue_with_its_labels(self):
        calls, fix = self.patrol([finding()])
        posted = [body for path, method, body in calls if method == "POST"]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["labels"], ["bug", "services/api"])
        self.assertEqual(posted[0]["assignees"], ["ana"])
        posts = [c.args[0] for c in self.say.call_args_list]
        self.assertIn("opened <https://github.com/owner/repo/issues/4800|#4800>", posts[0])
        self.assertIn("owner ana", posts[0])
        self.assertIn("patrol done: 1 findings, opened", posts[-1])
        self.assertIn("2026-10-07 to 2026-10-14", posted[0]["body"])
        self.assertEqual(ledger.load(self.store, ledger.fingerprint(finding()))["issue"], 4800)
        fix.assert_not_called()

    def test_a_blocked_patrol_posts_why_and_opens_nothing(self):
        report = {"outcome": "blocked", "reason": "the patrol wrote no tmp/valac/patrol.json",
                  "totals": {"costUsd": 5.5, "durationMs": 840000}}
        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac, "apply_inbox"), \
             mock.patch.object(valac, "run", return_value=report), mock.patch.object(valac, "open_issue") as opened, \
             mock.patch.object(valac.axiom, "emit"), mock.patch.object(valac, "say") as say:
            valac.patrol()
        opened.assert_not_called()
        self.assertIn("ended blocked: the patrol wrote no", say.call_args.args[0])
        self.assertIn("5.50 USD · 14 min", say.call_args.args[0])

    def test_the_second_run_on_the_same_finding_opens_nothing(self):
        self.patrol([finding()])
        calls, _ = self.patrol([finding()])
        self.assertEqual([c for c in calls if c[1] == "POST"], [])

    def test_with_the_pr_switch_on_the_fix_runs_but_not_for_human_only(self):
        first, human = finding(eventType="a_failed"), finding(eventType="b_failed", humanOnly=True)
        _, fix = self.patrol([first, human], can_open_pr=True)
        self.assertEqual([c.args[0]["fingerprint"] for c in fix.call_args_list], [ledger.fingerprint(first)])

    def test_a_finding_an_open_pr_names_opens_nothing_and_stays_out_of_the_ledger(self):
        calls, _ = self.patrol([finding()], open_prs=(4790,))
        self.assertEqual([c for c in calls if c[1] == "POST"], [])
        self.assertTrue(any('is%3Apr' in c[0] and "checkout_total_failed" in c[0] for c in calls))
        self.assertIsNone(ledger.load(self.store, ledger.fingerprint(finding())))

    def test_an_unsafe_finding_opens_nothing(self):
        calls, _ = self.patrol([finding(cause="see a.buyer@shop.example")])
        self.assertEqual([c for c in calls if c[1] == "POST"], [])
        self.assertIsNone(ledger.load(self.store, ledger.fingerprint(finding())))


PLAN = {"outcome": "plan", "plan": {"files": ["services/api/src/a.ts", "services/api/src/a.test.ts (new)"]}}
PATCH = "diff --git a/services/api/src/a.ts b/services/api/src/a.ts\n"


def commit(login, parents=1):
    return {"author": {"login": login}, "parents": [{}] * parents}


class OwnerTest(unittest.TestCase):
    def owner(self, commits, team=("ana", "maxbo")):
        with mock.patch.object(valac, "github", return_value=commits), \
             mock.patch.object(valac, "team_logins", return_value=set(team)):
            return valac.owner("services/api/src/a.ts")

    def test_the_engineer_with_most_commits_owns_the_issue(self):
        commits = [commit("maxbo"), commit("ana"), commit("ana")]
        self.assertEqual(self.owner(commits), "ana")

    def test_merges_bots_and_people_outside_the_team_do_not_count(self):
        commits = [commit("maxbo"), commit("darkanum", parents=2), commit("darkanum", parents=2),
                   commit("dependabot[bot]"), commit("dependabot[bot]"), {"author": None, "parents": [{}]}]
        self.assertEqual(self.owner(commits, team=("maxbo", "darkanum")), "maxbo")

    def test_no_commit_and_no_file_give_no_owner(self):
        self.assertIsNone(self.owner([]))
        self.assertIsNone(valac.owner(""))

    def test_a_failed_lookup_still_opens_the_issue(self):
        calls = []

        def github(path, *args, method="GET", body=None, **kwargs):
            calls.append(body)
            return {"number": 4800} if method == "POST" else [{"name": "bug"}]

        with mock.patch.object(valac, "github", side_effect=github), \
             mock.patch.object(valac, "owner", side_effect=RuntimeError("rate limited")), \
             mock.patch.object(valac.axiom, "emit") as emit:
            self.assertEqual(valac.open_issue(finding()), (4800, ""))
        self.assertNotIn("assignees", calls[-1])
        self.assertEqual(emit.call_args.args[1], "valac.owner.failed")


class NotifyTest(unittest.TestCase):
    def test_a_failed_post_is_a_warn_line_without_the_url(self):
        error = valac.urllib.error.HTTPError("https://hooks.slack.com/services/SECRET", 404, "no_service", {}, None)
        with mock.patch.object(valac.notify, "post", side_effect=error), mock.patch.object(valac.axiom, "emit") as emit:
            valac.say("hello", "patrol-20261016")
        self.assertEqual(emit.call_args.args[1], "valac.slack.failed")
        self.assertNotIn("SECRET", repr(emit.call_args))

    def test_slack_markup_in_a_title_is_escaped(self):
        text = valac.notify.issue_opened(4800, "a < b & <!channel>", {"score": 70, "dataset": "api"}, "")
        self.assertIn("a &lt; b &amp; &lt;!channel&gt;", text)
        self.assertIn("owner none", text)

    def test_no_webhook_file_posts_nothing(self):
        with mock.patch.object(valac.notify, "WEBHOOK_FILE", Path("/nonexistent/slack_webhook_url")):
            self.assertFalse(valac.notify.post("hello"))


class FixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store, self.runs = root / "findings", root / "runs"
        self.store.mkdir()
        ledger.opened(self.store, finding(), 4800, NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def fix(self, patch, result=None, plan=None, pr_error=None):
        run_dir = self.runs / "4800-20261014"
        run_dir.mkdir(parents=True)
        (run_dir / "fix.patch").write_text(patch)
        (run_dir / "base.sha").write_text("a" * 40)
        ready = result or {"outcome": "ready", "prTitle": "fix: retry expired Database transactions",
                           "prBody": "## 📝 TL;DR\n\nRetry.\n\nCo-Authored-By: Claude <a@b>"}
        with mock.patch.object(valac, "STORE", self.store), mock.patch.object(valac, "RUNS", self.runs), \
             mock.patch.object(valac, "run", side_effect=[plan or PLAN, ready]), \
             mock.patch.object(valac, "open_pull_request", return_value={"pr": 4801, "url": "u"},
                               side_effect=pr_error) as opened, \
             mock.patch.object(valac.axiom, "emit") as self.emit:
            valac.fix(finding(), 4800)
        return opened, ledger.load(self.store, "aaaa1111")

    def test_a_patch_inside_its_plan_opens_the_pr_that_closes_the_issue(self):
        opened, record = self.fix(PATCH)
        service, issue, base, branch, _, title, body = opened.call_args.args
        self.assertEqual((service, issue, branch), ("valac", "4800", "fix/issue-4800-retry-expired-database-transactions"))
        self.assertEqual(title, "fix: retry expired Database transactions")
        self.assertIn("Closes #4800", body)
        self.assertNotIn("Co-Authored-By", body)
        self.assertEqual((record["state"], record["pr"]), ("pr_open", 4801))

    def test_a_patch_outside_its_plan_opens_nothing(self):
        opened, record = self.fix("diff --git a/frontend/main/src/b.ts b/frontend/main/src/b.ts\n")
        opened.assert_not_called()
        self.assertEqual((record["state"], record["lastOutcome"]), ("issue_open", "blocked"))

    def test_a_guarded_path_opens_nothing_even_inside_the_plan(self):
        plan = {"outcome": "plan", "plan": {"files": [".github/workflows/x.yml"]}}
        opened, record = self.fix("diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml\n", plan=plan)
        opened.assert_not_called()
        self.assertIn("must never change", record["lastReason"])

    def test_a_run_that_is_not_ready_opens_nothing(self):
        opened, record = self.fix(PATCH, {"outcome": "cannot reproduce", "reason": "no signal"})
        opened.assert_not_called()
        self.assertEqual(record["lastOutcome"], "cannot reproduce")


class FixFailedTest(unittest.TestCase):
    def test_a_crash_in_the_fix_flow_is_an_error_event(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(valac, "STORE", Path(tmp)), \
             mock.patch.object(valac.axiom, "emit") as emit:
            ledger.opened(Path(tmp), finding(), 4800, NOW)
            valac.fix_failed(finding(), 4800, RuntimeError("push refused"))
            record = ledger.load(Path(tmp), "aaaa1111")
        event = emit.call_args
        self.assertEqual(event.args[1], "valac.fix.failed")
        self.assertEqual((event.kwargs["level"], event.kwargs["errorType"]), ("error", "RuntimeError"))
        self.assertEqual(record["lastOutcome"], "failed")


class FingerprintTest(unittest.TestCase):
    def test_the_host_ignores_the_model_fingerprint(self):
        self.assertEqual(ledger.fingerprint(finding("aaaa1111")), ledger.fingerprint(finding("bbbb2222")))

    def test_another_class_for_the_same_event_is_the_same_defect(self):
        self.assertEqual(ledger.fingerprint(finding()), ledger.fingerprint(finding(**{"class": "noise"})))

    def test_another_file_is_another_defect(self):
        other = finding(location={"file": "services/api/src/b.ts", "line": 1})
        self.assertNotEqual(ledger.fingerprint(finding()), ledger.fingerprint(other))


class SwitchTest(unittest.TestCase):
    def test_disabled_runs_nothing(self):
        with mock.patch.object(valac, "ENABLED", False), mock.patch.object(valac, "patrol") as patrol:
            valac.main()
        patrol.assert_not_called()


if __name__ == "__main__":
    unittest.main()
