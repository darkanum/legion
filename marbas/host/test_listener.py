"""Run with: python3 -m unittest test_listener (no dependencies). Slack and the host secrets are stubbed at import."""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MARBAS_CHANNELS", "C1")
# The test run installs nothing, so slack_sdk is a stub; listener.py only builds a client at import.
for name in ("slack_sdk", "slack_sdk.socket_mode", "slack_sdk.socket_mode.request", "slack_sdk.socket_mode.response"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["slack_sdk"].WebClient = mock.MagicMock
sys.modules["slack_sdk.socket_mode"].SocketModeClient = object
sys.modules["slack_sdk.socket_mode.request"].SocketModeRequest = object
sys.modules["slack_sdk.socket_mode.response"].SocketModeResponse = object
with mock.patch.object(Path, "read_text", return_value="token"):
    import listener  # noqa: E402

import state  # noqa: E402

PLAN = {"plan": {"files": ["apps/web/src/a.tsx"], "packages": ["apps/web"]}}
PATCH_IN_PLAN = "diff --git a/apps/web/src/a.tsx b/apps/web/src/a.tsx\n"
PATCH_WIDER = PATCH_IN_PLAN + "diff --git a/apps/web/src/b.tsx b/apps/web/src/b.tsx\n"
KEY = "C1-1.0"


class ThreadCase(unittest.TestCase):
    """A thread folder on a temporary disk, with Slack, the run log and the issue assignment stubbed."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "threads" / KEY).mkdir(parents=True)
        plan_file = self.root / "plan1.json"
        plan_file.write_text(json.dumps(PLAN))
        self.thread = {"issue": "7", "channel": "C1", "ts": "1.0", "round": 1, "pr": 9, "branch": "fix/issue-7-x",
                       "plan_file": str(plan_file), "review_attempts": 1, "review": {"reviewer": "eng", "id": 11}}
        self.said = []
        for target, value in ((state, "STATE"), (listener, "STATE")):
            patcher = mock.patch.object(target, value, self.root / "threads")
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(listener, "RUNS", self.root / "runs"),
                        mock.patch.object(listener.axiom, "emit"), mock.patch.object(listener.log, "exception"),
                        mock.patch.object(listener, "say", side_effect=lambda thread, text, blocks=None:
                                          self.said.append(text) or "ts"),
                        mock.patch.object(listener.pr, "assign_issue", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_dir(self, patch: str) -> Path:
        run_dir = self.root / "runs" / "7-1"
        run_dir.mkdir(parents=True)
        (run_dir / "fix.patch").write_text(patch)
        (run_dir / "result.json").write_text(json.dumps({"outcome": "ready", "reviewFix": "rename"}))
        return run_dir


class ListenerTest(ThreadCase):
    def test_a_review_run_that_needs_a_decision_goes_to_the_reviewer(self):
        for outcome in ("needs design", "blocked"):
            with self.subTest(outcome=outcome):
                self.said.clear()
                state.file(KEY).unlink(missing_ok=True)
                listener.after_review(KEY, dict(self.thread), {"outcome": outcome, "reason": "pick a name"})
                self.assertEqual(state.load(KEY).get("stage"), "needs_human")
                self.assertIn(listener.HANDOFF, self.said[-1])
        listener.pr.assign_issue.assert_called_with("7", "eng")

    def test_a_ready_review_outside_the_plan_asks_for_the_wider_scope(self):
        self.run_dir(PATCH_WIDER)
        with mock.patch.object(listener.pr, "push_fix") as push:
            listener.after_review(KEY, dict(self.thread), {"outcome": "ready"})
        push.assert_not_called()
        saved = state.load(KEY)
        self.assertEqual((saved["stage"], saved["scope_extra"]), ("awaiting_scope", ["apps/web/src/b.tsx"]))

    def test_a_failed_review_push_goes_to_a_human(self):
        run_dir = self.run_dir(PATCH_IN_PLAN)
        for failure in ({"return_value": {"error": "conflict"}}, {"side_effect": OSError("down")}):
            with self.subTest(failure=failure):
                # Each case starts clean, so it cannot pass on what the case before it saved or posted.
                self.said.clear()
                state.file(KEY).unlink(missing_ok=True)
                with mock.patch.object(listener.pr, "push_fix", **failure):
                    listener.push_review(KEY, dict(self.thread), {"reviewFix": "rename"}, run_dir)
                self.assertEqual(state.load(KEY).get("stage"), "needs_human")
                self.assertIn(listener.HANDOFF, self.said[-1])

    def test_a_pushed_review_goes_back_to_the_pr_watch_and_asks_the_reviewer(self):
        run_dir = self.run_dir(PATCH_IN_PLAN)
        with mock.patch.object(listener.pr, "push_fix", return_value={"sha": "abc1234"}), \
             mock.patch.object(listener.pr, "answer_review", return_value=True) as answer:
            listener.push_review(KEY, dict(self.thread, ready_told=True), {"reviewFix": "rename"}, run_dir)
        saved = state.load(KEY)
        self.assertEqual((saved["stage"], saved["ready_told"]), ("pr_open", False))
        answer.assert_called_once_with(9, "eng", "rename", "abc1234", 1)
        self.assertIn("asked `eng`", self.said[-1])

    def test_an_approved_wider_scope_on_a_review_pushes_instead_of_opening_a_pr(self):
        run_dir = self.run_dir(PATCH_WIDER)
        thread = dict(self.thread, stage="awaiting_scope", scope_extra=["apps/web/src/b.tsx"], run_dir=str(run_dir),
                      scope_ts="ts")
        with mock.patch.object(listener, "jobs") as jobs:
            listener.on_scope(KEY, thread, "U1", approved=True)
        self.assertEqual(state.load(KEY)["stage"], "pushing_review")
        jobs.put.assert_called_once_with((KEY, "push_review"))

    def test_a_failed_ci_push_goes_to_a_human(self):
        self.run_dir(PATCH_IN_PLAN)
        with mock.patch.object(listener.pr, "push_fix", return_value={"error": "conflict"}):
            listener.after_ci(KEY, dict(self.thread), {"outcome": "ready", "ciFix": "fix the test"})
        self.assertEqual(state.load(KEY)["stage"], "needs_human")


class TriggerTest(ThreadCase):
    LINK = f"<https://github.com/{listener.REPO}/issues/7|#7>"
    ISSUE = {"state": "open", "labels": [{"name": "bug"}]}

    def setUp(self):
        super().setUp()
        listener.BOT_ID = "B0"
        for patcher in (mock.patch.object(listener, "engineers", return_value={"U1"}),
                        mock.patch.object(listener, "github", return_value=self.ISSUE),
                        mock.patch.object(listener.pr, "open_prs_for", return_value=[]),
                        mock.patch.object(listener, "claim"), mock.patch.object(listener, "web"),
                        mock.patch.object(listener, "jobs")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, user="U1", text=None, thread_ts=None, **settings):
        event = {"channel": "C1", "ts": "1.0", "user": user, "text": text or f"Bug: {self.LINK}"}
        if thread_ts:
            event["thread_ts"] = thread_ts
        with mock.patch.multiple(listener, **{"TRIGGER_USERS": set(), "TRIGGER_TEXT": "", **settings}):
            listener.on_message(event)
        return state.load(KEY)

    def test_an_engineer_link_at_the_top_of_the_channel_starts_a_plan(self):
        saved = self.post()
        self.assertEqual((saved["issue"], saved["stage"]), ("7", "planning"))
        listener.jobs.put.assert_called_once_with((KEY, "plan"))

    def test_a_person_who_is_not_an_engineer_starts_nothing(self):
        self.assertEqual(self.post(user="U9"), {})

    def test_a_link_to_another_repository_starts_nothing(self):
        self.assertEqual(self.post(text="<https://github.com/other/repo/issues/7>"), {})

    def test_with_trigger_users_set_only_they_start_outside_a_test_channel(self):
        self.assertEqual(self.post(TRIGGER_USERS={"UBOT"}), {})
        state.file(KEY).unlink(missing_ok=True)
        self.assertEqual(self.post(user="UBOT", TRIGGER_USERS={"UBOT"})["stage"], "planning")

    def test_with_trigger_text_the_parent_must_hold_it_and_come_from_the_poster(self):
        listener.web.conversations_replies.return_value = {"messages": [{"user": "U1", "text": "hello"}]}
        self.assertEqual(self.post(thread_ts="1.0", TRIGGER_TEXT="New bug report:"), {})
        listener.web.conversations_replies.return_value = {"messages": [{"user": "U1", "text": "New bug report: x"}]}
        self.assertEqual(self.post(thread_ts="1.0", TRIGGER_TEXT="New bug report:")["stage"], "planning")

    def test_with_trigger_text_a_top_level_link_starts_nothing(self):
        self.assertEqual(self.post(TRIGGER_TEXT="New bug report:"), {})


class HandoffTest(unittest.TestCase):
    def test_a_handoff_pings_the_oncall_only_when_one_is_set(self):
        thread = {"channel": "C1", "ts": "1.0"}
        for oncall, expected in (("<!subteam^S1>", "<!subteam^S1>"), ("", None)):
            with self.subTest(oncall=oncall), mock.patch.object(listener, "ONCALL", oncall), \
                    mock.patch.object(listener, "web") as web:
                listener.say(thread, f"stop. {listener.HANDOFF}")
                text = web.chat_postMessage.call_args.kwargs["text"]
                if expected:
                    self.assertIn(expected, text)
                else:
                    self.assertIn(listener.HANDOFF, text)


if __name__ == "__main__":
    unittest.main()
