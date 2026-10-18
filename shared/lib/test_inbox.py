"""Run with: python3 -m unittest test_inbox (no dependencies)."""

import json
import tempfile
import unittest
from pathlib import Path

import inbox


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def event(self, delivery, number=12):
        return {"delivery": delivery, "event": "pull_request", "action": "opened", "number": number, "sha": "a" * 40,
                "received_at": "2026-10-08T10:00:00Z", "extra": "dropped"}

    def test_write_keeps_only_the_contract_keys(self):
        path = inbox.write("orobas", self.event("d1"), "1-d1.json", self.root)
        self.assertEqual(set(json.loads(path.read_text())), set(inbox.KEYS))
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_drain_returns_events_in_name_order_and_empties_the_inbox(self):
        inbox.write("orobas", self.event("late"), "2-late.json", self.root)
        inbox.write("orobas", self.event("early"), "1-early.json", self.root)
        events = inbox.drain("orobas", self.root)
        self.assertEqual([e["delivery"] for e in events], ["early", "late"])
        self.assertEqual(inbox.drain("orobas", self.root), [])

    def test_a_bad_file_moves_to_bad_and_the_rest_still_drains(self):
        folder = inbox.folder("marbas", self.root)
        folder.mkdir(parents=True)
        (folder / "1-bad.json").write_text("{not json")
        (folder / "2-wrong.json").write_text(json.dumps({"delivery": "x", "event": "e", "number": "12"}))
        inbox.write("marbas", self.event("ok"), "3-ok.json", self.root)
        self.assertEqual([e["delivery"] for e in inbox.drain("marbas", self.root)], ["ok"])
        self.assertEqual(sorted(p.name for p in (folder / "bad").iterdir()), ["1-bad.json", "2-wrong.json"])

    def test_received_epoch_reads_the_hooks_time(self):
        self.assertEqual(inbox.received_epoch(self.event("d")), 1791453600.0)
        self.assertIsNone(inbox.received_epoch({"delivery": "d"}))
        self.assertIsNone(inbox.received_epoch({"received_at": "yesterday"}))

    def test_since_ms_counts_from_the_event_and_never_goes_negative(self):
        self.assertEqual(inbox.since_ms(100.0, now=100.25), 250)
        self.assertEqual(inbox.since_ms(100.0, now=99.0), 0)
        self.assertIsNone(inbox.since_ms(None))

    def test_a_missing_inbox_drains_to_nothing(self):
        self.assertEqual(inbox.drain("nobody", self.root), [])

    def test_one_failing_event_never_stops_the_rest_of_the_batch(self):
        for name in ("1-a", "2-b", "3-c"):
            inbox.write("orobas", self.event(name[2:]), f"{name}.json", self.root)
        handled, failed = [], []

        def handle(event):
            if event["delivery"] == "a":
                raise RuntimeError("boom")
            handled.append(event["delivery"])

        inbox.consume("orobas", handle, lambda event, error: failed.append(event["delivery"]), self.root)
        self.assertEqual((handled, failed), (["b", "c"], ["a"]))
        self.assertEqual(inbox.drain("orobas", self.root), [])


if __name__ == "__main__":
    unittest.main()
