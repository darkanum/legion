"""Run with: python3 -m unittest test_paths (no dependencies)."""

import unittest
from unittest import mock

import paths


class AgentsOnHostTest(unittest.TestCase):
    def test_no_value_runs_every_agent(self):
        with mock.patch.dict("os.environ", {"LEGION_AGENTS": ""}):
            self.assertIsNone(paths.agents_on_host())

    def test_a_comma_list_names_the_agents(self):
        with mock.patch.dict("os.environ", {"LEGION_AGENTS": "valac,orobas"}):
            self.assertEqual(paths.agents_on_host(), {"valac", "orobas"})


if __name__ == "__main__":
    unittest.main()
