"""Run with: python3 -m unittest (no dependencies)."""

import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import hooks
import inbox

SECRET = b"test-secret"
AGENTS_DIR = Path(__file__).resolve().parents[3]


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


class VerifyTest(unittest.TestCase):
    def test_the_right_signature_passes(self):
        self.assertTrue(hooks.verify(SECRET, b"{}", sign(b"{}")))

    def test_a_wrong_or_missing_signature_fails(self):
        self.assertFalse(hooks.verify(SECRET, b"{}", sign(b"{ }")))
        self.assertFalse(hooks.verify(SECRET, b"{}", None))
        self.assertFalse(hooks.verify(SECRET, b"{}", "sha1=abc"))


class SummarizeTest(unittest.TestCase):
    def test_a_pull_request_names_the_pr_and_its_head(self):
        payload = {"action": "synchronize", "pull_request": {"number": 7, "head": {"sha": "b" * 40}, "body": "ignore me"}}
        summary = hooks.summarize("pull_request", "d1", payload)
        self.assertEqual((summary["number"], summary["sha"], summary["action"]), (7, "b" * 40, "synchronize"))
        self.assertNotIn("ignore me", json.dumps(summary))

    def test_a_check_suite_takes_its_first_pr_or_none(self):
        payload = {"action": "completed", "check_suite": {"head_sha": "c" * 40, "pull_requests": [{"number": 9}]}}
        self.assertEqual(hooks.summarize("check_suite", "d", payload)["number"], 9)
        payload["check_suite"]["pull_requests"] = []
        self.assertIsNone(hooks.summarize("check_suite", "d", payload)["number"])

    def test_a_deployment_status_carries_the_deployed_sha(self):
        summary = hooks.summarize("deployment_status", "d", {"action": "created", "deployment": {"sha": "d" * 40}})
        self.assertEqual((summary["number"], summary["sha"]), (None, "d" * 40))

    def test_an_issue_event_names_the_issue(self):
        summary = hooks.summarize("issues", "d", {"action": "closed", "issue": {"number": 12, "body": "ignore me"}})
        self.assertEqual((summary["number"], summary["sha"], summary["action"]), (12, None, "closed"))
        self.assertNotIn("ignore me", json.dumps(summary))

    def test_a_number_that_is_not_an_int_is_dropped(self):
        summary = hooks.summarize("pull_request", "d", {"pull_request": {"number": "7"}})
        self.assertIsNone(summary["number"])


class RoutingTest(unittest.TestCase):
    subs = hooks.load_subscriptions(AGENTS_DIR)

    def test_every_agent_with_a_subscriptions_file_is_found(self):
        self.assertEqual(sorted(self.subs), ["marbas", "orobas", "valac"])

    def test_a_vm_with_one_agent_loads_only_that_agent(self):
        self.assertEqual(sorted(hooks.load_subscriptions(AGENTS_DIR, {"orobas"})), ["orobas"])

    def test_each_agent_gets_only_its_events(self):
        self.assertEqual(hooks.subscribers_for(self.subs, "pull_request", "synchronize"), ["orobas"])
        self.assertEqual(hooks.subscribers_for(self.subs, "pull_request", "closed"), ["marbas", "orobas", "valac"])
        self.assertEqual(hooks.subscribers_for(self.subs, "deployment_status", "created"), ["marbas"])
        self.assertEqual(hooks.subscribers_for(self.subs, "issues", "opened"), [])
        self.assertEqual(hooks.subscribers_for(self.subs, "issues", "closed"), ["valac"])

    def test_the_webhook_spec_lists_the_union(self):
        self.assertEqual(hooks.events(self.subs), ["check_suite", "deployment_status", "issues", "pull_request",
                                                   "pull_request_review", "workflow_run"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(inbox, "WORKSPACE", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        emit = mock.patch.object(hooks.axiom, "emit")
        self.emit = emit.start()
        self.addCleanup(emit.stop)
        hooks.Handler.secret = SECRET
        subs = mock.patch.dict(hooks.SUBSCRIPTIONS, hooks.load_subscriptions(AGENTS_DIR))
        subs.start()
        self.addCleanup(subs.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), hooks.Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, body: bytes, signature: str | None, event="pull_request") -> int:
        headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": "d-1", "Content-Type": "application/json"}
        if signature:
            headers["X-Hub-Signature-256"] = signature
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/webhooks/github", data=body,
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    def test_a_signed_event_lands_in_each_subscriber_inbox(self):
        body = json.dumps({"action": "closed", "pull_request": {"number": 3, "head": {"sha": "e" * 40}}}).encode()
        self.assertEqual(self.post(body, sign(body)), 202)
        for agent in ("orobas", "marbas"):
            self.assertEqual([e["number"] for e in inbox.drain(agent, self.root)], [3])

    def test_the_received_event_names_how_many_agents_got_it(self):
        logged = threading.Event()
        self.emit.side_effect = lambda *args, **kwargs: logged.set()
        body = json.dumps({"action": "closed", "pull_request": {"number": 3, "head": {"sha": "e" * 40}}}).encode()
        self.assertEqual(self.post(body, sign(body)), 202)
        self.assertTrue(logged.wait(1))  # the handler logs right after it answers
        self.assertEqual(self.emit.call_args.kwargs["subscribers"], 3)

    def test_a_bad_signature_is_refused_and_writes_nothing(self):
        body = b'{"action": "opened"}'
        self.assertEqual(self.post(body, sign(b"other")), 401)
        self.assertEqual(inbox.drain("orobas", self.root), [])
        self.assertEqual(self.emit.call_args.args[1], "hooks.rejected")

    def test_a_body_over_the_limit_is_refused(self):
        with mock.patch.object(hooks, "MAX_BODY", 10):
            body = b'{"action": "opened", "x": 1}'
            self.assertEqual(self.post(body, sign(body)), 413)

    def test_a_ping_is_accepted_and_routed_nowhere(self):
        body = b'{"zen": "hi"}'
        self.assertEqual(self.post(body, sign(body), event="ping"), 202)
        self.assertEqual(list(self.root.glob("*/inbox/*.json")), [])


class RedeliverTest(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp()) / "state.json"

    def run_with(self, *pages):
        read = []

        def paged(path):
            for page in pages:
                read.append(page)
                yield page

        with mock.patch.object(hooks.gh, "pages", side_effect=paged), mock.patch.object(hooks.gh, "github") as call:
            sent = hooks.redeliver("42", self.state)
        return sent, [c.args[0].split("/")[-2] for c in call.call_args_list], len(read)

    def test_the_first_run_only_records_the_mark(self):
        sent, posted, read = self.run_with([{"id": 5, "status_code": 500}], [{"id": 4, "status_code": 500}])
        self.assertEqual((sent, posted, read), (0, [], 1))
        self.assertEqual(json.loads(self.state.read_text()), {"last_delivery_id": 5})

    def test_failed_deliveries_after_the_mark_are_sent_again(self):
        self.state.write_text(json.dumps({"last_delivery_id": 10}))
        deliveries = [{"id": 13, "status_code": 502}, {"id": 12, "status_code": 202},
                      {"id": 11, "status_code": 0, "redelivery": True}, {"id": 9, "status_code": 500}]
        sent, posted, _ = self.run_with(deliveries)
        self.assertEqual((sent, posted), (1, ["13"]))
        self.assertEqual(json.loads(self.state.read_text()), {"last_delivery_id": 13})

    def test_it_pages_back_to_the_mark_and_sends_the_oldest_first(self):
        self.state.write_text(json.dumps({"last_delivery_id": 10}))
        sent, posted, read = self.run_with([{"id": 15, "status_code": 500}, {"id": 14, "status_code": 202}],
                                           [{"id": 12, "status_code": 500}, {"id": 9, "status_code": 500}],
                                           [{"id": 8, "status_code": 500}])
        self.assertEqual((sent, posted, read), (2, ["12", "15"], 2))
        self.assertEqual(json.loads(self.state.read_text()), {"last_delivery_id": 15})


if __name__ == "__main__":
    unittest.main()
