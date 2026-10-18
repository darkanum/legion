"""Run with: python3 -m unittest test_sandbox_run (no dependencies)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sandbox_run


def exited(code, stdout=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class SandboxRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, *answers, pattern="7-*"):
        with mock.patch.object(sandbox_run.subprocess, "run", side_effect=list(answers)) as called, \
             mock.patch.object(sandbox_run.time, "sleep") as slept:
            result, run_dir = sandbox_run.sandbox_run("run.sh", ["7", "--plan"], self.runs, pattern)
        return result, run_dir, called, slept

    def test_it_waits_while_a_hand_run_holds_the_lock(self):
        result, _, called, slept = self.run_with(exited(sandbox_run.RUN_LOCKED), exited(0, '{"outcome": "plan"}'))
        self.assertEqual(result, {"outcome": "plan"})
        self.assertEqual(called.call_count, 2)
        slept.assert_called_once_with(sandbox_run.LOCK_WAIT_SECONDS)

    def test_output_that_is_not_json_is_a_blocked_run(self):
        result, run_dir, _, _ = self.run_with(exited(1, "Traceback"))
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("exited 1", result["reason"])
        self.assertIsNone(run_dir)

    def test_it_returns_the_newest_folder_that_matches_and_skips_files(self):
        old, new, other = self.runs / "7-1", self.runs / "7-2", self.runs / "8-3"
        for folder in (old, new, other):
            folder.mkdir()
        (self.runs / "7-plan.json").write_text("{}")
        for age, folder in enumerate((other, new, old)):
            os.utime(folder, (1000 - age, 1000 - age))
        os.utime(new, (2000, 2000))
        _, run_dir, _, _ = self.run_with(exited(0, "{}"))
        self.assertEqual(run_dir, new)


if __name__ == "__main__":
    unittest.main()
