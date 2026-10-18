"""Run with: python3 -m unittest test_axiom (no dependencies)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import axiom


class EmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token = Path(self.tmp.name) / "token"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_record_carries_the_shared_logger_fields(self):
        self.token.write_text("t")
        sent = []
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(axiom, "TOKEN_FILE", self.token), \
             mock.patch("urllib.request.urlopen", side_effect=lambda request, timeout: sent.append(request) or response):
            axiom.emit("marbas", "marbas.run.finished", "plan run", "C1-1.2", issue=4011, score=None, outcome="plan")
        record = json.loads(sent[0].data)[0]
        for field in ("_time", "level", "type", "message", "environment", "service", "module", "traceId"):
            self.assertIn(field, record)
        self.assertEqual(record["service"], "marbas")
        self.assertEqual(record["traceId"], "C1-1.2")
        self.assertNotIn("score", record)  # an empty field is left out, not sent as null

    def test_no_token_sends_nothing(self):
        with mock.patch.object(axiom, "TOKEN_FILE", self.token), mock.patch("urllib.request.urlopen") as urlopen:
            axiom.emit("marbas", "marbas.thread.stage", "x", "k")
        urlopen.assert_not_called()

    def test_a_failed_ingest_never_raises(self):
        self.token.write_text("t")
        with mock.patch.object(axiom, "TOKEN_FILE", self.token), \
             mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            axiom.emit("marbas", "marbas.job.failed", "x", "k", level="error")

    def test_run_fields_read_claude_json(self):
        run_dir = Path(self.tmp.name)
        (run_dir / "claude.json").write_text(json.dumps({"duration_ms": 245083, "num_turns": 33, "total_cost_usd": 1.46}))
        self.assertEqual(axiom.run_fields(run_dir), {"durationMs": 245083, "turns": 33, "costUsd": 1.46})
        self.assertEqual(axiom.run_fields(run_dir / "missing"), {})

    def test_run_fields_sum_the_stage_files(self):
        run_dir = Path(self.tmp.name)
        (run_dir / "claude-gate1.json").write_text(json.dumps({"duration_ms": 100, "num_turns": 3, "total_cost_usd": 0.5}))
        (run_dir / "claude-gate2.json").write_text(json.dumps({"duration_ms": 50, "num_turns": 2, "total_cost_usd": 0.25}))
        (run_dir / "claude-lane-ai.json").write_text("not json")
        self.assertEqual(axiom.run_fields(run_dir), {"durationMs": 150, "turns": 5, "costUsd": 0.75})


if __name__ == "__main__":
    unittest.main()
