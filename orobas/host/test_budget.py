"""Run with: python3 -m unittest test_budget (no dependencies)."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import budget


def epoch(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "budget.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_cap_is_35_percent_of_the_measured_window(self):
        self.assertAlmostEqual(budget.CAP_USD, 56.0)

    def test_spend_older_than_the_window_does_not_count(self):
        budget.record(self.file, 50.0, 0.0)
        budget.record(self.file, 10.0, budget.WINDOW_SECONDS - 1)
        self.assertEqual(budget.blocked(self.file, budget.WINDOW_SECONDS - 1), budget.CAP)
        self.assertIsNone(budget.blocked(self.file, budget.WINDOW_SECONDS + 1))

    def test_a_pause_blocks_until_its_time(self):
        budget.pause(self.file, 100.0)
        self.assertEqual(budget.blocked(self.file, 99.0), budget.SEAT_LIMIT)
        self.assertIsNone(budget.blocked(self.file, 101.0))

    def test_the_reset_time_in_the_429_text_is_the_next_one(self):
        text = "You've hit your session limit · resets 3:30pm (UTC)"
        self.assertEqual(budget.reset_at(text, epoch("2026-10-13T13:45:00")), epoch("2026-10-13T15:30:00"))
        self.assertEqual(budget.reset_at(text, epoch("2026-10-13T16:00:00")), epoch("2026-10-14T15:30:00"))
        self.assertEqual(budget.reset_at("resets 12am (UTC)", epoch("2026-10-13T23:00:00")), epoch("2026-10-14T00:00:00"))

    def test_a_429_with_no_reset_time_waits_an_hour(self):
        self.assertEqual(budget.reset_at("limit", 0.0), budget.NO_RESET_SECONDS)

    def test_a_run_with_a_429_names_it_and_a_run_without_does_not(self):
        run = Path(self.tmp.name) / "run"
        run.mkdir()
        (run / "claude-gate1.json").write_text(json.dumps({"api_error_status": 429, "result": "resets 3:30pm (UTC)",
                                                           "total_cost_usd": 0}))
        (run / "claude-gate2.json").write_text(json.dumps({"total_cost_usd": 0.25}))
        self.assertEqual(budget.limit_hit(run), "resets 3:30pm (UTC)")
        self.assertAlmostEqual(budget.run_cost(run), 0.25)
        (run / "claude-gate1.json").unlink()
        self.assertIsNone(budget.limit_hit(run))


    def test_a_run_sums_cached_input_output_and_cost(self):
        run = Path(self.tmp.name) / "run2"
        run.mkdir()
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 9000,
                 "output_tokens": 500}
        for stage in ("gate1", "gate2"):
            (run / f"claude-{stage}.json").write_text(json.dumps({"usage": usage, "total_cost_usd": 4.0}))
        self.assertEqual(budget.run_usage(run), {"tokensIn": 20020, "tokensOut": 1000, "costUsd": 8.0, "windowPct": 5.0})
    def test_ten_full_reviews_a_day_then_none_until_the_next_day(self):
        day = epoch("2026-10-14T12:00:00")
        for _ in range(budget.DAILY_FULL_REVIEWS):
            self.assertTrue(budget.take_full_review(self.file, day))
        self.assertFalse(budget.take_full_review(self.file, day))
        self.assertTrue(budget.take_full_review(self.file, epoch("2026-10-15T00:00:01")))

    def test_a_review_that_did_not_run_frees_its_place(self):
        day = epoch("2026-10-14T12:00:00")
        for _ in range(budget.DAILY_FULL_REVIEWS):
            budget.take_full_review(self.file, day)
        budget.give_back_full_review(self.file, day)
        self.assertEqual(budget.full_reviews(self.file, day), budget.DAILY_FULL_REVIEWS - 1)


    def test_a_file_that_does_not_parse_or_has_no_usage_adds_nothing_but_its_cost(self):
        run = Path(self.tmp.name) / "run3"
        run.mkdir()
        (run / "claude-gate1.json").write_text("{not json")
        (run / "claude-gate2.json").write_text(json.dumps({"total_cost_usd": 0.5}))
        self.assertEqual(budget.run_usage(run), {"tokensIn": 0, "tokensOut": 0, "costUsd": 0.5, "windowPct": 0.3})
        self.assertAlmostEqual(budget.run_cost(run), 0.5)


if __name__ == "__main__":
    unittest.main()
