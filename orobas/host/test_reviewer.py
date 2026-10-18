"""Run with: python3 -m unittest test_reviewer (no dependencies)."""

import json
import queue
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import gate0
import report
import reviewer
import stages

SHA, NEW = "a" * 40, "b" * 40


class Base(unittest.TestCase):
    """A temporary state folder, a fresh job queue, and no Axiom or Docker call."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(reviewer, "STATE", Path(self.tmp.name) / "prs"),
                        mock.patch.object(reviewer, "BUDGET", Path(self.tmp.name) / "budget.json"),
                        mock.patch.dict(reviewer._held, clear=True),
                        mock.patch.object(reviewer, "jobs", queue.Queue()),
                        mock.patch.object(reviewer.axiom, "emit"),
                        mock.patch.object(reviewer, "kill"),
                        mock.patch.object(reviewer, "me", return_value="legion-bot"),
                        mock.patch.object(reviewer.log, "exception")]
        self.mocks = [p.start() for p in self.patches]
        self.emit, self.kill = self.mocks[4], self.mocks[5]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def put(self, number, **state):
        reviewer.STATE.mkdir(parents=True, exist_ok=True)
        (reviewer.STATE / f"{number}.json").write_text(json.dumps(state))


class MigrateTest(unittest.TestCase):
    def test_the_old_flat_files(self):
        self.assertEqual(reviewer.migrate({"reviewed_sha": SHA, "reviewed_at": "t"})["stage"], "done")
        self.assertEqual(reviewer.migrate({"seen_sha": SHA, "seen_at": 5.0})["stage"], "waiting")
        failed = {"seen_sha": SHA, "failed_sha": SHA, "failures": reviewer.MAX_FAILURES}
        self.assertEqual(reviewer.migrate(failed)["stage"], "failed")
        self.assertEqual(reviewer.migrate({"sha": SHA, "stage": "running"})["stage"], "running")


class SwitchTest(Base):
    def test_switched_off_the_events_stay_in_the_inbox(self):
        with mock.patch.object(reviewer, "ENABLED", False), mock.patch.object(reviewer, "inbox_module") as inbox, \
                mock.patch.object(reviewer, "release") as release:
            reviewer.tick(100.0)
        inbox.return_value.consume.assert_not_called()
        release.assert_not_called()

    def test_switched_on_the_inbox_is_read_and_quiet_heads_are_released(self):
        with mock.patch.object(reviewer, "ENABLED", True), mock.patch.object(reviewer, "inbox_module") as inbox, \
                mock.patch.object(reviewer, "release") as release:
            reviewer.tick(100.0)
        self.assertEqual(inbox.return_value.consume.call_args.args[0], "orobas")
        release.assert_called_once_with(100.0)


class SaveTest(Base):
    def test_a_stage_change_emits_once(self):
        reviewer.update(7, sha=SHA, stage="waiting")
        reviewer.update(7, seen_at=1.0)
        types = [c.args[1] for c in self.emit.call_args_list]
        self.assertEqual(types, ["orobas.stage"])


class StateFileTest(Base):
    def test_a_save_leaves_no_temporary_file(self):
        reviewer.update(7, sha=SHA, stage="waiting")
        self.assertEqual(sorted(p.name for p in reviewer.STATE.iterdir()), ["7.json"])

    def test_prs_at_skips_a_half_written_file_and_still_finds_the_others(self):
        self.put(7, sha=SHA, stage="done")
        self.put(9, sha=SHA, stage="done")
        (reviewer.STATE / "8.json").write_text('{"sha": "' + SHA + '", "stage": "do')

        with mock.patch.object(reviewer.log, "warning"):
            self.assertEqual(reviewer.prs_at(SHA, None), [7, 9])
        self.assertEqual(self.emit.call_args.args[1], "orobas.state.unreadable")
        self.assertEqual(self.emit.call_args.kwargs["level"], "warn")


class HandleTest(Base):
    def event(self, action, sha=SHA, kind="pull_request", number=7):
        reviewer.handle({"event": kind, "action": action, "number": number, "sha": sha}, 1000.0)
        return reviewer.load(number) if isinstance(number, int) else None

    def test_a_push_starts_the_quiet_timer(self):
        state = self.event("synchronize")
        self.assertEqual((state["sha"], state["stage"], state["seen_at"]), (SHA, "waiting", 1000.0))

    def test_a_new_push_replaces_a_running_review(self):
        self.put(7, sha=SHA, stage="running")
        state = self.event("synchronize", NEW)
        self.kill.assert_called_once_with(7)
        self.assertEqual((state["sha"], state["stage"]), (NEW, "waiting"))

    def test_the_same_head_again_changes_nothing(self):
        self.put(7, sha=SHA, stage="done")
        self.assertEqual(self.event("opened")["stage"], "done")

    def test_a_review_request_runs_the_ladder_again_now(self):
        self.put(7, sha=SHA, stage="done")
        state = self.event("review_requested")
        self.assertEqual((state["stage"], state["seen_at"]), ("waiting", 1000.0 - reviewer.QUIET_SECONDS))

    def test_a_body_edit_re_runs_only_a_blocked_pr(self):
        self.put(7, sha=SHA, stage="done", event="REQUEST_CHANGES")
        self.assertEqual(self.event("edited")["stage"], "waiting")
        self.put(7, sha=SHA, stage="done", event="COMMENT")
        self.assertEqual(self.event("labeled")["stage"], "done")

    def test_a_push_after_a_full_review_gets_the_light_check_from_the_reviewed_head(self):
        self.put(7, sha=SHA, stage="done", report_comment_id=5)
        state = self.event("synchronize", NEW)
        self.assertEqual((state["mode"], state["since_sha"]), ("recheck", SHA))

    def test_a_second_push_while_waiting_keeps_the_head_the_check_starts_from(self):
        self.put(7, sha=SHA, stage="done", report_comment_id=5)
        self.event("synchronize", NEW)
        state = self.event("synchronize", "c" * 40)
        self.assertEqual((state["mode"], state["since_sha"]), ("recheck", SHA))

    def test_a_push_after_a_gate_stop_or_no_review_gets_the_full_ladder(self):
        self.put(7, sha=SHA, stage="stopped", stoppedAt="Gate 1", report_comment_id=5)
        self.assertEqual(self.event("synchronize", NEW)["mode"], "full")
        self.put(8, sha=SHA, stage="waiting")
        self.assertEqual(self.event("synchronize", NEW, number=8)["mode"], "full")

    def test_a_review_request_runs_the_full_ladder(self):
        self.put(7, sha=SHA, stage="done", mode="recheck", since_sha="c" * 40)
        state = self.event("review_requested")
        self.assertEqual((state["mode"], state["since_sha"]), ("full", None))

    def test_ready_for_review_after_a_draft(self):
        self.put(7, sha=SHA, stage="skipped")
        self.assertEqual(self.event("ready_for_review")["stage"], "waiting")

    def test_a_closed_pr_stops_its_run(self):
        self.put(7, sha=SHA, stage="running")
        self.assertEqual(self.event("closed")["stage"], "closed")
        self.kill.assert_called_once_with(7)

    def test_the_head_keeps_the_time_github_sent_the_event(self):
        reviewer.handle({"event": "pull_request", "action": "synchronize", "number": 7, "sha": SHA,
                         "received_at": "2026-10-08T10:00:00Z"}, 2e9)
        self.assertEqual(reviewer.load(7)["event_at"], 1791453600.0)

    def test_an_event_with_no_time_falls_back_to_the_read_time(self):
        self.assertEqual(self.event("synchronize")["event_at"], 1000.0)

    def test_a_finished_check_suite_queues_a_recheck(self):
        self.put(7, sha=SHA, stage="done")
        self.put(8, sha=NEW, stage="done")
        reviewer.handle({"event": "check_suite", "action": "completed", "number": None, "sha": SHA}, 1.0)
        self.assertEqual(reviewer.jobs.get_nowait(), ("recheck", 7))
        self.assertTrue(reviewer.jobs.empty())

    def test_a_check_suite_on_a_pr_orobas_never_saw_writes_nothing(self):
        reviewer.handle({"event": "check_suite", "action": "completed", "number": 9, "sha": SHA}, 1.0)
        self.assertTrue(reviewer.jobs.empty())
        self.assertEqual(reviewer.load(9), {})


class PostedTest(Base):
    def test_the_posted_event_reports_the_time_since_the_github_event(self):
        pull = {"head": {"sha": SHA}, "labels": []}
        decision = {"event": "COMMENT", "note": "", "inline": [], "extra": [], "blockers": 0,
                    "review": {"comments": [], "process": [], "resolved": []}}
        with mock.patch.object(reviewer, "judge", return_value=decision), \
                mock.patch.object(reviewer.report, "render", return_value="report"), \
                mock.patch.object(reviewer, "upsert_report", return_value=(1, "url")), \
                mock.patch.object(reviewer, "post_review"), mock.patch.object(reviewer, "clean_up"), \
                mock.patch.object(reviewer, "emit_gates"), mock.patch.object(reviewer.time, "time", return_value=1060.5):
            reviewer.post(7, pull, [], {}, {"lanes": {}}, None, "green", {"base": "main"}, {"event_at": 1000.0})
        posted = [c for c in self.emit.call_args_list if c.args[1] == "orobas.review.posted"]
        self.assertEqual(posted[0].kwargs["sinceEventMs"], 60500)

    def test_a_post_with_a_run_carries_the_usage_once(self):
        """axiom.run_fields and budget.run_usage both carry costUsd; two ** with one key would raise."""
        pull = {"head": {"sha": SHA}, "labels": []}
        decision = {"event": "COMMENT", "note": "", "inline": [], "extra": [], "blockers": 0,
                    "review": {"comments": [], "process": [], "resolved": []}}
        run = Path(self.tmp.name) / "run"
        run.mkdir()
        (run / "claude-gate1.json").write_text(json.dumps({"total_cost_usd": 1.6, "num_turns": 3, "duration_ms": 10,
                                                           "usage": {"input_tokens": 100, "output_tokens": 20}}))
        with mock.patch.object(reviewer, "judge", return_value=decision), \
                mock.patch.object(reviewer, "upsert_report", return_value=(1, "url")) as upsert, \
                mock.patch.object(reviewer, "post_review"), mock.patch.object(reviewer, "clean_up"), \
                mock.patch.object(reviewer, "emit_gates"):
            reviewer.post(7, pull, [], {}, {"lanes": {}, "status": {"stages": []}}, run, "green", {"base": "main"}, {})
        posted = [c for c in self.emit.call_args_list if c.args[1] == "orobas.review.posted"][0].kwargs
        self.assertEqual((posted["tokensIn"], posted["tokensOut"], posted["costUsd"], posted["windowPct"]), (100, 20, 1.6, 1.0))
        self.assertIn("🧾 This review: 120 tokens", upsert.call_args.args[2])


class JudgeTest(Base):
    """STD-01 must reach REQUEST_CHANGES through judge, with blocking off, or the PR only gets a comment."""

    def decide(self, **meta):
        meta = {"title": "feat(api): x", "body": "", "base": reviewer.config.BASE_BRANCH, "head": "feat/x",
                "linkedIssues": [], "labels": [], "mergeable": True, "commits": [], **meta}
        ladder = {"gate0": gate0.check(meta, [], set()), "lanes": {}}
        pull = {"user": {"login": "someone", "type": "User"}}
        with mock.patch.object(reviewer, "CAN_BLOCK", False), mock.patch.object(reviewer, "CAN_APPROVE", False):
            return reviewer.judge(pull, [], {}, ladder, "green", meta)["event"]

    def test_no_issue_requests_changes_with_blocking_off(self):
        self.assertEqual(self.decide(), "REQUEST_CHANGES")

    def test_a_link_or_the_label_lifts_the_block(self):
        self.assertNotEqual(self.decide(linkedIssues=[1]), "REQUEST_CHANGES")
        self.assertNotEqual(self.decide(labels=["no-issue"]), "REQUEST_CHANGES")

    def test_another_blocker_only_comments_with_blocking_off(self):
        self.assertEqual(self.decide(linkedIssues=[1], base="dev"), "COMMENT")


class SubscriptionsTest(unittest.TestCase):
    def test_the_hooks_service_sends_every_action_the_reviewer_handles(self):
        subs = json.loads((Path(reviewer.__file__).resolve().parents[1] / "subscriptions.json").read_text())
        wanted = set(reviewer.REVIEW_ACTIONS) | set(reviewer.UNBLOCK_ACTIONS)
        self.assertLessEqual(wanted, set(subs["events"]["pull_request"]))


class ReleaseTest(Base):
    def test_a_quiet_head_is_queued_once(self):
        self.put(7, sha=SHA, stage="waiting", seen_at=0.0)
        self.put(8, sha=SHA, stage="waiting", seen_at=50.0)
        reviewer.release(reviewer.QUIET_SECONDS)
        reviewer.release(reviewer.QUIET_SECONDS)
        self.assertEqual(reviewer.jobs.get_nowait(), ("review", 7))
        self.assertTrue(reviewer.jobs.empty())
        self.assertEqual(reviewer.load(7)["stage"], "queued")


    def test_nothing_is_queued_past_the_cap_and_the_hold_is_said_once(self):
        self.put(7, sha=SHA, stage="waiting", seen_at=0.0)
        reviewer.budget.record(reviewer.BUDGET, reviewer.budget.CAP_USD, 0.0)
        reviewer.release(reviewer.QUIET_SECONDS)
        reviewer.release(reviewer.QUIET_SECONDS + 1)
        self.assertTrue(reviewer.jobs.empty())
        self.assertEqual(reviewer.load(7)["stage"], "waiting")
        held = [c for c in self.emit.call_args_list if c.args[1] == "orobas.budget.reached"]
        self.assertEqual([c.kwargs["reason"] for c in held], [reviewer.budget.CAP])

    def test_the_hold_ends_when_the_window_moves_on(self):
        self.put(7, sha=SHA, stage="waiting", seen_at=0.0)
        reviewer.budget.record(reviewer.BUDGET, reviewer.budget.CAP_USD, 0.0)
        reviewer.release(reviewer.budget.WINDOW_SECONDS + 1)
        self.assertEqual(reviewer.jobs.get_nowait(), ("review", 7))


    def test_past_the_daily_limit_only_light_checks_are_queued_oldest_pr_first(self):
        for _ in range(reviewer.budget.DAILY_FULL_REVIEWS - 1):
            reviewer.budget.take_full_review(reviewer.BUDGET, reviewer.QUIET_SECONDS)
        self.put(1000, sha=SHA, stage="waiting", seen_at=0.0)
        self.put(999, sha=SHA, stage="waiting", seen_at=0.0)
        self.put(1001, sha=SHA, stage="waiting", seen_at=0.0, mode="recheck", since_sha=NEW)
        reviewer.release(reviewer.QUIET_SECONDS)
        queued = [reviewer.jobs.get_nowait()[1] for _ in range(reviewer.jobs.qsize())]
        self.assertEqual(queued, [999, 1001])
        self.assertEqual(reviewer.load(1000)["stage"], "waiting")
        held = [c.kwargs["reason"] for c in self.emit.call_args_list if c.args[1] == "orobas.budget.reached"]
        self.assertEqual(held, [reviewer.budget.DAILY])


class RecoverTest(Base):
    def test_a_cut_review_runs_again(self):
        self.put(7, sha=SHA, stage="running", failures=1)
        self.put(8, sha=SHA, stage="done")
        reviewer.recover()
        self.kill.assert_called_once_with(7)
        self.assertEqual((reviewer.load(7)["stage"], reviewer.load(7)["failures"]), ("waiting", 1))


class WorkTest(Base):
    def test_two_failures_on_a_head_stop_it(self):
        self.put(7, sha=SHA, stage="queued")
        with mock.patch.object(reviewer, "review", side_effect=RuntimeError("boom")):
            for _ in range(2):
                reviewer.jobs.put(("review", 7))
                with mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
                    with self.assertRaises(SystemExit):
                        reviewer.work()
        self.assertEqual(reviewer.load(7)["stage"], "failed")
        failed = [c for c in self.emit.call_args_list if c.args[1] == "orobas.review.failed"]
        self.assertEqual(len(failed), 2)

    def test_a_run_failure_sends_its_text_and_any_other_error_only_its_type(self):
        for error, message in ((reviewer.RunFailed("run.sh exited 137"), "run.sh exited 137"),
                               (ValueError("output that can hold a secret"), "ValueError")):
            self.emit.reset_mock()
            self.put(7, sha=SHA, stage="queued", failures=0)
            reviewer.jobs.put(("review", 7))
            with mock.patch.object(reviewer, "review", side_effect=error),                     mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
                with self.assertRaises(SystemExit):
                    reviewer.work()
            failed = [c for c in self.emit.call_args_list if c.args[1] == "orobas.review.failed"]
            self.assertEqual(failed[0].kwargs["errorMessage"], message)

    def test_a_claude_api_error_warns_until_the_last_try_and_any_other_failure_pages(self):
        api = reviewer.ApiFailed("gate1 did not finish")
        for error, before, level in ((api, 0, "warn"), (api, reviewer.MAX_FAILURES - 1, "error"),
                                     (reviewer.RunFailed("x"), 0, "error")):
            self.emit.reset_mock()
            self.put(7, sha=SHA, stage="queued", failures=before)
            reviewer.jobs.put(("review", 7))
            with mock.patch.object(reviewer, "review", side_effect=error),                     mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
                with self.assertRaises(SystemExit):
                    reviewer.work()
            failed = [c for c in self.emit.call_args_list if c.args[1] == "orobas.review.failed"]
            self.assertEqual(failed[0].kwargs["level"], level)

    def test_a_seat_limit_parks_the_pr_with_no_failure_and_pauses_new_reviews(self):
        self.put(7, sha=SHA, stage="queued", failures=0)
        reviewer.jobs.put(("review", 7))
        error = reviewer.SeatLimit("You've hit your session limit", until=10_000.0)
        with mock.patch.object(reviewer, "review", side_effect=error),                 mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
            with self.assertRaises(SystemExit):
                reviewer.work()
        self.assertEqual((reviewer.load(7)["stage"], reviewer.load(7)["failures"]), ("waiting", 0))
        names = [c.args[1] for c in self.emit.call_args_list]
        self.assertIn("orobas.review.parked", names)
        self.assertNotIn("orobas.review.failed", names)
        self.assertEqual(reviewer.budget.blocked(reviewer.BUDGET, 9_000.0), reviewer.budget.SEAT_LIMIT)

    def test_a_failure_after_a_push_does_not_count_against_the_new_head(self):
        self.put(7, sha=SHA, stage="queued", failures=0)
        newer = "b" * 40

        def push_then_fail(number):
            reviewer.update(number, sha=newer, stage="waiting", failures=0)
            raise RuntimeError("GitHub 502")

        reviewer.jobs.put(("review", 7))
        with mock.patch.object(reviewer, "review", side_effect=push_then_fail), \
                mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
            with self.assertRaises(SystemExit):
                reviewer.work()
        self.assertEqual((reviewer.load(7)["sha"], reviewer.load(7)["failures"]), (newer, 0))

    def test_a_failed_recheck_keeps_the_stage_and_does_not_review_again(self):
        self.put(7, sha=SHA, stage="done", failures=0)
        reviewer.jobs.put(("recheck", 7))
        with mock.patch.object(reviewer, "recheck", side_effect=RuntimeError("boom")), \
                mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]):
            with self.assertRaises(SystemExit):
                reviewer.work()
        self.assertEqual((reviewer.load(7)["stage"], reviewer.load(7)["failures"]), ("done", 0))
        types = [c.args[1] for c in self.emit.call_args_list]
        self.assertIn("orobas.recheck.failed", types)
        self.assertNotIn("orobas.review.failed", types)


class SupersededTest(Base):
    """A newer push or a close kills the running container: that run is not a failure."""

    def run_review(self, during, before_run=None):
        pull = {"number": 7, "state": "open", "draft": False, "head": {"sha": SHA, "repo": {"full_name": reviewer.REPO}},
                "labels": [], "user": {"login": "eng"}}

        def cut(*_):
            during()
            raise RuntimeError("the run wrote no status.json")

        with mock.patch.object(reviewer.gh, "github", return_value=pull), \
                mock.patch.object(reviewer.gh, "team_logins", return_value={"eng"}), \
                mock.patch.object(reviewer.gh, "checks_state", return_value="pending"), \
                mock.patch.object(reviewer, "paged", return_value=[]), \
                mock.patch.object(reviewer, "pr_graph", return_value={}), \
                mock.patch.object(reviewer, "pr_meta", return_value={}), \
                mock.patch.object(reviewer.context, "refresh"), \
                mock.patch.object(reviewer, "run_gate0", side_effect=before_run or (lambda *_: {})), \
                mock.patch.object(reviewer, "run_ladder", side_effect=cut):
            reviewer.review(7)

    def emitted(self, kind):
        return [c for c in self.emit.call_args_list if c.args[1] == kind]

    def test_a_newer_push_leaves_the_new_head_alone(self):
        self.put(7, sha=SHA, stage="queued")
        self.run_review(lambda: reviewer.handle({"event": "pull_request", "action": "synchronize", "number": 7,
                                                 "sha": NEW}, 1000.0))
        state = reviewer.load(7)
        self.assertEqual((state["sha"], state["stage"], state["failures"]), (NEW, "waiting", 0))
        self.assertEqual(len(self.emitted("orobas.review.superseded")), 1)
        self.assertEqual(self.emitted("orobas.review.failed"), [])

    def test_the_kill_comes_after_the_new_state_is_saved(self):
        # The run ends as soon as kill() removes its container, and the worker then reads the state.
        for event, expected in (({"action": "synchronize", "sha": NEW}, (NEW, "waiting")),
                                ({"action": "closed"}, (SHA, "closed"))):
            self.put(7, sha=SHA, stage="running")
            seen = []
            self.kill.side_effect = lambda n: seen.append((reviewer.load(n)["sha"], reviewer.load(n)["stage"]))
            reviewer.handle({"event": "pull_request", "number": 7, **event}, 1000.0)
            self.assertEqual(seen, [expected])

    def test_a_push_during_the_reads_is_never_marked_running(self):
        self.put(7, sha=SHA, stage="queued")
        push = {"event": "pull_request", "action": "synchronize", "number": 7, "sha": NEW}
        self.run_review(lambda: self.fail("the ladder ran"), before_run=lambda *_: reviewer.handle(push, 1000.0) or {})
        state = reviewer.load(7)
        self.assertEqual((state["sha"], state["stage"]), (NEW, "waiting"))
        self.assertEqual(len(self.emitted("orobas.review.superseded")), 1)

    def test_a_closed_pr_stays_closed(self):
        self.put(7, sha=SHA, stage="queued")
        self.run_review(lambda: reviewer.handle({"event": "pull_request", "action": "closed", "number": 7}, 1000.0))
        self.assertEqual(reviewer.load(7)["stage"], "closed")
        self.assertEqual(self.emitted("orobas.review.failed"), [])

    def test_a_failure_after_the_close_does_not_reopen_the_pr(self):
        self.put(7, sha=SHA, stage="closed")
        reviewer.jobs.put(("review", 7))
        with mock.patch.object(reviewer, "review", side_effect=RuntimeError("boom")), \
                mock.patch.object(reviewer.jobs, "get", side_effect=[reviewer.jobs.get_nowait(), SystemExit]), \
                mock.patch.object(reviewer.log, "warning"):
            with self.assertRaises(SystemExit):
                reviewer.work()
        self.assertEqual(reviewer.load(7)["stage"], "closed")
        self.assertEqual(self.emitted("orobas.review.failed"), [])


class ReviewSkipTest(Base):
    def test_a_draft_is_skipped(self):
        self.put(7, sha=SHA, stage="queued")
        pull = {"number": 7, "state": "open", "draft": True, "head": {"sha": SHA, "repo": {"full_name": reviewer.REPO}},
                "labels": [], "user": {"login": "eng"}}
        with mock.patch.object(reviewer.gh, "github", return_value=pull), \
                mock.patch.object(reviewer.gh, "team_logins", return_value={"eng"}):
            reviewer.review(7)
        self.assertEqual(reviewer.load(7)["stage"], "skipped")

    def skip_draft(self, **state):
        self.put(7, sha=SHA, stage="queued", **state)
        pull = {"number": 7, "state": "open", "draft": True, "head": {"sha": SHA, "repo": {"full_name": reviewer.REPO}},
                "labels": [], "user": {"login": "eng"}}
        with mock.patch.object(reviewer.gh, "github", return_value=pull), \
                mock.patch.object(reviewer.gh, "team_logins", return_value={"eng"}):
            reviewer.review(7)

    def test_a_skipped_full_review_gives_its_daily_place_back(self):
        reviewer.budget.take_full_review(reviewer.BUDGET, reviewer.time.time())
        self.skip_draft()
        self.assertEqual(reviewer.budget.full_reviews(reviewer.BUDGET, reviewer.time.time()), 0)

    def test_a_skipped_light_check_leaves_the_daily_count_alone(self):
        reviewer.budget.take_full_review(reviewer.BUDGET, reviewer.time.time())
        self.skip_draft(mode=reviewer.MODE_RECHECK, since_sha=NEW)
        self.assertEqual(reviewer.budget.full_reviews(reviewer.BUDGET, reviewer.time.time()), 1)


class LabelsTest(Base):
    def test_ensure_creates_the_missing_and_fixes_the_changed(self):
        existing = [{"name": "legion:skip", "color": "000000", "description": "old"}]
        calls = []
        with mock.patch.object(reviewer, "paged", return_value=existing), \
                mock.patch.object(reviewer.gh, "github", side_effect=lambda path, **kw: calls.append((kw.get("method"), path))):
            reviewer.ensure_labels()
        self.assertIn(("PATCH", f"repos/{reviewer.REPO}/labels/legion%3Askip"), calls)
        self.assertEqual(sum(method == "POST" for method, _ in calls), len(stages.LANES))

    def test_set_lane_labels_adds_and_removes(self):
        calls = []
        with mock.patch.object(reviewer.gh, "github", side_effect=lambda path, **kw: calls.append((kw.get("method"), path, kw.get("body")))):
            reviewer.set_lane_labels(7, ["security"], ["review:tests", "backend/api"])
        self.assertEqual(calls, [("POST", f"repos/{reviewer.REPO}/issues/7/labels", {"labels": ["review:security"]}),
                                 ("DELETE", f"repos/{reviewer.REPO}/issues/7/labels/review%3Atests", None)])


class ReportTest(Base):
    def test_the_report_is_found_by_its_marker_and_edited(self):
        found = [{"id": 5, "user": {"login": "legion-bot"}, "body": "x " + report.MARKER + "e30= -->"}]
        calls = []

        def github(path, **kw):
            calls.append((kw.get("method"), path))
            return {"id": 5, "html_url": "u"}

        with mock.patch.object(reviewer, "paged", return_value=found), mock.patch.object(reviewer.gh, "github", side_effect=github):
            self.assertEqual(reviewer.upsert_report(7, None, "body"), (5, "u"))
        self.assertEqual(calls, [("PATCH", f"repos/{reviewer.REPO}/issues/comments/5")])

    def test_a_lost_comment_is_created_again(self):
        error = urllib.error.HTTPError("u", 404, "gone", {}, None)
        calls = []

        def github(path, **kw):
            calls.append((kw.get("method"), path))
            if kw.get("method") == "PATCH":
                raise error
            return {"id": 9, "html_url": "u"}

        with mock.patch.object(reviewer, "paged", return_value=[]), mock.patch.object(reviewer.gh, "github", side_effect=github):
            self.assertEqual(reviewer.upsert_report(7, 5, "body"), (9, "u"))
        self.assertEqual(calls[-1], ("POST", f"repos/{reviewer.REPO}/issues/7/comments"))


class LadderTest(unittest.TestCase):
    def write(self, folder, stages_list, **files):
        (folder / "in").mkdir()
        (folder / "in" / "gate0.json").write_text(json.dumps({"comments": [], "process": []}))
        (folder / "status.json").write_text(json.dumps({"stages": stages_list, "stoppedAt": files.pop("stopped", None)}))
        for name, data in files.items():
            (folder / f"{name.replace('_', '-')}.json").write_text(json.dumps(data))

    def test_a_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.write(folder, [{"id": "gate1", "status": "pass"}, {"id": "gate2", "status": "pass"},
                                {"id": "lane-security", "status": "pass"}, {"id": "lane-tests", "status": "timeout"},
                                {"id": "gate3", "status": "pass"}],
                       gate1={"verdict": "pass", "comments": []}, gate2={"lanes": ["tests", "security", "evil"]},
                       lane_security={"comments": []}, gate3={"verdict": "pass", "comments": []})
            ladder = reviewer.load_ladder(folder)
            self.assertEqual(list(ladder["lanes"]), ["security"])
            self.assertEqual(ladder["status"]["lanes"], ["security", "tests"])
            self.assertEqual(reviewer.incomplete(ladder), "lane-tests")

    def test_a_gate1_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.write(folder, [{"id": "gate1", "status": "fail"}, {"id": "gate2", "status": "skipped"}],
                       gate1={"verdict": "fail", "comments": []}, stopped="gate1")
            ladder = reviewer.load_ladder(folder)
            self.assertEqual(ladder["status"]["stoppedAt"], "Gate 1")
            self.assertEqual(reviewer.incomplete(ladder), "")

    def test_a_broken_gate1_or_a_run_error_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.write(folder, [{"id": "gate1", "status": "timeout", "reason": "slow"}])
            with self.assertRaisesRegex(RuntimeError, "gate1 did not finish"):
                reviewer.load_ladder(folder)
            (folder / "status.json").write_text(json.dumps({"stages": [], "error": "the review hit the 12-minute limit"}))
            with self.assertRaisesRegex(RuntimeError, "12-minute"):
                reviewer.load_ladder(folder)

    def test_a_claude_api_error_raises_api_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.write(folder, [{"id": "gate1", "status": "error", "reason": "no gate1.json (exit 1)"}])
            with self.assertRaises(reviewer.RunFailed) as caught:
                reviewer.load_ladder(folder)
            self.assertNotIsInstance(caught.exception, reviewer.ApiFailed)
            (folder / "claude-gate1.json").write_text(json.dumps({"terminal_reason": "api_error"}))
            with self.assertRaises(reviewer.ApiFailed):
                reviewer.load_ladder(folder)


class InputsTest(unittest.TestCase):
    def test_the_inputs_have_the_names_that_review_sh_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            reviewer.write_inputs(Path(folder), {"number": 7}, [], {}, {"comments": [], "process": []})
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()),
                             ["diff.patch", "gate0.json", "mode", "pr.json", "stages.json", "threads.json"])
            self.assertEqual((Path(folder) / "mode").read_text(), "full")



class RecheckTest(Base):
    PULL = {"number": 7, "head": {"sha": NEW}}
    THREADS = {"reviewThreads": {"nodes": [{"id": "T1", "isResolved": False, "path": "a.py", "line": 3, "comments": {
        "nodes": [{"databaseId": 11, "author": {"login": "legion-bot"}, "body": "x"}]}}]}}
    CLEAN = {"blockers": 0, "comments": [], "process": []}

    def test_a_force_push_falls_back_to_the_full_ladder(self):
        with mock.patch.object(reviewer.gh, "github", return_value={"status": "diverged"}):
            self.assertIsNone(reviewer.run_recheck(7, self.PULL, [], self.THREADS, {}, self.CLEAN, SHA))

    def test_no_open_thread_needs_no_model(self):
        with mock.patch.object(reviewer.gh, "github", return_value={"status": "ahead", "files": [{"filename": "a.py"}]}), \
                mock.patch.object(reviewer.subprocess, "run") as run:
            run_dir, ladder = reviewer.run_recheck(7, self.PULL, [], {}, {}, self.CLEAN, SHA)
        run.assert_not_called()
        self.assertIsNone(run_dir)
        self.assertEqual(ladder["recheck"]["resolved"], [])

    def test_the_resolved_threads_count_as_fixed(self):
        ladder = {"gate0": self.CLEAN, "lanes": {}, "recheck": {"resolved": [11], "comments": [], "process": []}}
        review = reviewer.verdict.collect(ladder)
        self.assertEqual(review["resolved"], [11])
        self.assertEqual(reviewer.verdict.open_threads(self.THREADS["reviewThreads"]["nodes"], "legion-bot",
                                                       review["resolved"]), 0)

    def test_a_recheck_replaces_the_last_one_in_the_report(self):
        body = "## report\n\n" + reviewer.report.SIGNATURE + "\n<!-- meta -->"
        once = reviewer.report.with_recheck(body, "first")
        twice = reviewer.report.with_recheck(once, "second")
        self.assertNotIn("first", twice)
        self.assertEqual(twice.count(reviewer.report.RECHECK_START), 1)
        self.assertLess(twice.index("second"), twice.index(reviewer.report.SIGNATURE))
        self.assertTrue(twice.endswith("<!-- meta -->"))



class HumanReviewTest(Base):
    PULL = {"number": 7, "user": {"login": "dev"}}

    def reviewer_of(self, *reviews):
        with mock.patch.object(reviewer, "paged", return_value=list(reviews)):
            return reviewer.human_reviewer(self.PULL)

    def test_any_review_from_a_person_counts(self):
        for state in ("APPROVED", "COMMENTED", "CHANGES_REQUESTED"):
            self.assertEqual(self.reviewer_of({"user": {"login": "eng", "type": "User"}, "state": state}), "eng")

    def test_the_bot_the_author_and_other_bots_do_not_count(self):
        self.assertIsNone(self.reviewer_of({"user": {"login": "legion-bot", "type": "User"}},
                                           {"user": {"login": "dev", "type": "User"}},
                                           {"user": {"login": "ci[bot]", "type": "Bot"}}))


    def run_review(self, **state):
        self.put(7, sha=SHA, stage="queued", **state)
        pull = {"number": 7, "state": "open", "draft": False, "head": {"sha": SHA, "repo": {"full_name": reviewer.REPO}},
                "labels": [], "user": {"login": "dev"}}
        person = [{"user": {"login": "eng", "type": "User"}, "state": "COMMENTED"}]
        with mock.patch.object(reviewer.gh, "github", return_value=pull), \
                mock.patch.object(reviewer.gh, "team_logins", return_value={"dev"}), \
                mock.patch.object(reviewer, "paged", return_value=person), \
                mock.patch.object(reviewer.gh, "checks_state", side_effect=SystemExit):
            try:
                reviewer.review(7)
                return "skipped"
            except SystemExit:
                return "ran"

    def test_review_skips_a_full_review_a_person_reviewed(self):
        reviewer.budget.take_full_review(reviewer.BUDGET, reviewer.time.time())
        self.assertEqual(self.run_review(), "skipped")
        self.assertEqual(reviewer.load(7)["stage"], "skipped")
        self.assertEqual(reviewer.budget.full_reviews(reviewer.BUDGET, reviewer.time.time()), 0)

    def test_a_request_to_the_bot_or_a_light_check_still_runs(self):
        self.assertEqual(self.run_review(trigger="request"), "ran")
        self.assertEqual(self.run_review(mode="recheck", since_sha=NEW), "ran")

    def test_a_review_request_reopens_a_skipped_head(self):
        self.put(8, sha=SHA, stage="skipped")
        reviewer.handle({"event": "pull_request", "action": "review_requested", "number": 8, "sha": SHA}, 1000.0)
        self.assertEqual((reviewer.load(8)["stage"], reviewer.load(8)["trigger"]), ("waiting", "request"))


if __name__ == "__main__":
    unittest.main()
