"""Run with: python3 -m unittest test_events (no dependencies)."""

import unittest

import events

MERGE = "m" * 40


class OwnsTest(unittest.TestCase):
    def test_a_pr_event_moves_the_thread_of_that_pr(self):
        thread = {"stage": "pr_open", "pr": 12}
        self.assertTrue(events.owns(thread, {"number": 12, "sha": None}))
        self.assertFalse(events.owns(thread, {"number": 13, "sha": None}))

    def test_a_deploy_event_on_the_merge_commit_moves_a_deploying_thread(self):
        thread = {"stage": "deploying", "pr": 12, "merge_sha": MERGE}
        self.assertTrue(events.owns(thread, {"number": None, "sha": MERGE}))
        self.assertFalse(events.owns(thread, {"number": None, "sha": "x" * 40}))

    def test_the_merge_sha_does_not_move_a_thread_that_is_not_deploying(self):
        self.assertFalse(events.owns({"stage": "pr_open", "pr": 12, "merge_sha": MERGE}, {"number": None, "sha": MERGE}))

    def test_a_thread_outside_the_watched_stages_is_never_moved(self):
        self.assertFalse(events.owns({"stage": "proving", "pr": 12}, {"number": 12, "sha": None}))


class TimeoutsTest(unittest.TestCase):
    def test_only_the_passed_and_unchecked_marks_are_due(self):
        thread = {"stage": "deploying", "merged_at": 0, "timeouts_checked": [1200]}
        self.assertEqual(events.timeouts_due(thread, 1300, (1200, 5400)), [])
        self.assertEqual(events.timeouts_due(thread, 5401, (1200, 5400)), [5400])

    def test_a_thread_that_is_not_deploying_has_no_timeout(self):
        self.assertEqual(events.timeouts_due({"stage": "pr_open", "merged_at": 0}, 9999, (1200,)), [])


class DeliverTest(unittest.TestCase):
    def test_one_failing_thread_is_reported_and_the_others_still_move(self):
        threads = [("a", {"stage": "pr_open", "pr": 12}), ("b", {"stage": "pr_open", "pr": 12}),
                   ("c", {"stage": "pr_open", "pr": 13})]
        moved, failed = [], []

        def advance(key, event):
            if key == "a":
                raise RuntimeError("slack is down")
            moved.append(key)

        events.deliver({"number": 12, "sha": None}, threads, advance, lambda key, event, error: failed.append(key))
        self.assertEqual((moved, failed), (["b"], ["a"]))


if __name__ == "__main__":
    unittest.main()
