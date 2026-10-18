"""Run with: python3 -m unittest test_attribution (no dependencies)."""

import unittest

from github import ATTRIBUTION


class AttributionTest(unittest.TestCase):
    def test_an_ai_credit_line_matches(self):
        for line in ("🤖 Generated with [Claude Code](https://claude.com/claude-code)",
                     "Co-Authored-By: Claude <noreply@anthropic.com>", "Co-authored-by: Copilot <x@github.com>",
                     "Written by an AI assistant", "Created using ChatGPT"):
            self.assertRegex(line, ATTRIBUTION, line)

    def test_ordinary_text_and_human_co_authors_do_not(self):
        for line in ("Code generated with protoc from api.proto", "Co-authored-by: Ana Lima <ana@example.com>",
                     "fix: the Claude Code settings path", "see .claude/skills/x.md", "Generated with care"):
            self.assertNotRegex(line, ATTRIBUTION, line)


if __name__ == "__main__":
    unittest.main()
