"""Run with: python3 -m unittest test_state (no dependencies)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import state


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(state, "STATE", Path(self.tmp.name) / "threads"),
                        mock.patch.object(state.axiom, "emit"),
                        mock.patch.object(state.log, "warning")]
        self.emit, self.warning = [p.start() for p in self.patches][1:]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def put(self, key, text):
        folder = state.STATE / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "thread.json").write_text(text)


class SaveTest(Base):
    def test_it_writes_the_thread_and_leaves_no_temporary_file(self):
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        self.assertEqual(state.load("c1.1"), {"issue": 12, "stage": "pr_open"})
        self.assertEqual(sorted(p.name for p in (state.STATE / "c1.1").iterdir()), ["thread.json"])

    def test_a_second_save_replaces_the_file_instead_of_truncating_it(self):
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        before = state.file("c1.1").stat().st_ino
        state.save("c1.1", {"issue": 12, "stage": "deploying"})
        self.assertNotEqual(state.file("c1.1").stat().st_ino, before)
        self.assertEqual(json.loads(state.file("c1.1").read_text())["stage"], "deploying")

    def test_it_emits_only_when_the_stage_moved(self):
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        state.save("c1.1", {"issue": 12, "stage": "deploying"})
        self.assertEqual([call.args[1] for call in self.emit.call_args_list],
                         ["marbas.thread.stage", "marbas.thread.stage"])


class OpenThreadsTest(Base):
    def test_it_returns_every_thread_on_disk(self):
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        state.save("c2.2", {"issue": 13, "stage": "deploying"})
        self.assertEqual([key for key, _ in state.open_threads()], ["c1.1", "c2.2"])

    def test_a_half_written_file_is_skipped_and_the_others_still_come_back(self):
        state.save("c1.1", {"issue": 12, "stage": "pr_open"})
        self.put("c2.2", '{"issue": 13, "stage": "deplo')
        state.save("c3.3", {"issue": 14, "stage": "proving"})

        self.assertEqual([key for key, _ in state.open_threads()], ["c1.1", "c3.3"])
        self.warning.assert_called_once()
        self.assertEqual(self.emit.call_args.args[1], "marbas.state.unreadable")
        self.assertEqual(self.emit.call_args.kwargs["level"], "warn")

    def test_no_thread_folder_is_an_empty_list(self):
        self.assertEqual(state.open_threads(), [])


if __name__ == "__main__":
    unittest.main()
