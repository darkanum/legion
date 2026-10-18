"""Run with: python3 -m unittest test_context (no dependencies)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import context

SHA = "b" * 40


class PathsTest(unittest.TestCase):
    def test_guide_files_go_from_the_root_down(self):
        self.assertEqual(context.agents_files(["services/api/src/x.ts", "README.md"], ("AGENTS.md",)),
                         ["AGENTS.md", "services/AGENTS.md", "services/api/AGENTS.md", "services/api/src/AGENTS.md"])

    def test_every_guide_name_is_read_in_each_folder(self):
        self.assertEqual(context.agents_files(["api/x.go"], ("AGENTS.md", "CLAUDE.md")),
                         ["AGENTS.md", "CLAUDE.md", "api/AGENTS.md", "api/CLAUDE.md"])

    def test_new_exports_read_only_added_lines(self):
        files = [{"filename": "a.ts", "patch": "@@ -1 +1,3 @@\n+export const fooBar = 1\n-export function gone() {}\n+export type Thing = {}"}]
        self.assertEqual(context.new_exports(files), {"fooBar": "a.ts", "Thing": "a.ts"})

    def test_new_exports_in_go_and_python(self):
        files = [{"filename": "api/h.go", "patch": "@@ -0,0 +1,4 @@\n+func Handle(w http.ResponseWriter) {\n+func (s *Server) Start() error {\n"
                                                   "+func private() {}\n+type Config struct {"},
                 {"filename": "lib/m.py", "patch": "@@ -0,0 +1,3 @@\n+def load_settings():\n+class Store:\n+    def inner(self):"}]
        self.assertEqual(context.new_exports(files), {"Handle": "api/h.go", "Start": "api/h.go", "Config": "api/h.go",
                                                      "load_settings": "lib/m.py", "Store": "lib/m.py"})


class ConsumersTest(unittest.TestCase):
    def test_the_defining_file_is_not_a_consumer(self):
        hits = f"{SHA}:a.ts\n{SHA}:b.ts\n{SHA}:c/d.ts\n"
        with mock.patch.object(context, "git", return_value=hits):
            self.assertEqual(context.consumers(SHA, {"fooBar": "a.ts"}),
                             {"fooBar": {"definedIn": "a.ts", "files": ["b.ts", "c/d.ts"], "count": 2}})

    def test_no_match_is_zero(self):
        with mock.patch.object(context, "git", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertEqual(context.consumers(SHA, {"lonely": "a.ts"})["lonely"]["count"], 0)


class BuildTest(unittest.TestCase):
    def test_build_writes_the_files_the_agents_and_the_counts(self):
        texts = {"x/a.ts": "export const fooBar = 1\n", "AGENTS.md": "root rules", "x/AGENTS.md": "x rules"}
        files = [{"filename": "x/a.ts", "status": "added", "patch": "@@ -0,0 +1 @@\n+export const fooBar = 1"},
                 {"filename": "old.ts", "status": "removed", "patch": ""},
                 {"filename": "../escape.ts", "status": "added", "patch": ""}]
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(context, "show", side_effect=lambda sha, path: texts.get(path)), \
                mock.patch.object(context, "consumers", return_value={"fooBar": {"count": 0}}):
            context.build(Path(folder), SHA, files)
            self.assertEqual((Path(folder) / "files/x/a.ts").read_text(), texts["x/a.ts"])
            self.assertFalse((Path(folder) / "files/old.ts").exists())
            self.assertFalse((Path(folder).parent / "escape.ts").exists())
            agents = (Path(folder) / "agents.md").read_text()
            self.assertLess(agents.index("root rules"), agents.index("x rules"))
            self.assertEqual(json.loads((Path(folder) / "consumers.json").read_text()), {"fooBar": {"count": 0}})


if __name__ == "__main__":
    unittest.main()
