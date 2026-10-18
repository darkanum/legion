"""Run with: python3 -m unittest test_jsonstore (no dependencies)."""

import json
import tempfile
import unittest
from pathlib import Path

import jsonstore


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_it_creates_the_folder_and_writes_the_document(self):
        path = jsonstore.write(self.root / "deep" / "state.json", {"stage": "queued"})
        self.assertEqual(json.loads(path.read_text()), {"stage": "queued"})

    def test_it_leaves_no_temporary_file_behind(self):
        jsonstore.write(self.root / "state.json", {"stage": "queued"}, indent=2)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["state.json"])

    def test_it_replaces_the_file_instead_of_truncating_it(self):
        # A truncating write keeps the inode, so a new inode proves the rename: a reader sees the old or the new file.
        path = jsonstore.write(self.root / "state.json", {"stage": "done"})
        before = path.stat().st_ino
        jsonstore.write(path, {"stage": "running"})
        self.assertNotEqual(path.stat().st_ino, before)
        self.assertEqual(json.loads(path.read_text()), {"stage": "running"})

    def test_a_failed_write_keeps_the_old_document_and_no_temporary_file(self):
        path = jsonstore.write(self.root / "state.json", {"stage": "done"})
        with self.assertRaises(TypeError):
            jsonstore.write(path, {"stage": object()})
        self.assertEqual(json.loads(path.read_text()), {"stage": "done"})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["state.json"])


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_it_returns_the_document(self):
        self.assertEqual(jsonstore.read(jsonstore.write(self.root / "a.json", {"pr": 12})), {"pr": 12})

    def test_a_missing_file_is_none(self):
        self.assertIsNone(jsonstore.read(self.root / "gone.json"))

    def test_a_half_written_file_is_none(self):
        (self.root / "bad.json").write_text('{"stage": "run')
        self.assertIsNone(jsonstore.read(self.root / "bad.json"))

    def test_an_empty_file_is_none(self):
        (self.root / "empty.json").write_text("")
        self.assertIsNone(jsonstore.read(self.root / "empty.json"))

    def test_a_document_that_is_not_an_object_is_none(self):
        (self.root / "list.json").write_text("[1, 2]")
        self.assertIsNone(jsonstore.read(self.root / "list.json"))


if __name__ == "__main__":
    unittest.main()
