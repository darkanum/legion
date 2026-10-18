"""Run with: python3 -m unittest test_memory (no dependencies)."""

import tempfile
import unittest
from pathlib import Path

import memory


class NoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, words="Prove it on Playwright", lesson="Prove a layout fix in a real browser"):
        return memory.write_note("plan-reject", "4025", ["@apps/web"], "darkanum", words, lesson,
                                 "Plan round 1 for #4025.", folder=self.folder)

    def test_a_note_names_the_engineer_the_packages_and_the_lesson(self):
        text = self.note().read_text()
        self.assertIn("engineer: darkanum", text)
        self.assertIn('packages: ["@apps/web"]', text)
        self.assertIn("Prove a layout fix in a real browser", text)
        self.assertIn("> Prove it on Playwright", text)

    def test_a_note_never_keeps_an_email(self):
        text = self.note(words="Ask buyer@shop.example first", lesson="Mail ana.b+x@example.com").read_text()
        self.assertNotIn("@shop.example", text)
        self.assertNotIn("example.com", text)
        self.assertIn("[email]", text)

    def test_two_notes_on_one_day_do_not_overwrite(self):
        first, second = self.note(), self.note()
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.folder.glob("*.md"))), 2)


if __name__ == "__main__":
    unittest.main()
