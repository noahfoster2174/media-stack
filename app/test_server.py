#!/usr/bin/env python3
"""Unit tests for server.py's pure logic. Stdlib only: `python3 -m unittest test_server`.

Covers the brittle, regex/parsing-heavy bits that are easy to regress:
  - _clean_folder_name  (scene-name -> Radarr lookup term)
  - _ollama_model       (chat model resolution / auto-pick)
Importing server.py is side-effect-light (it only serves under __main__), but it does
read ../.env and init the SQLite DB, so run this from the app/ directory.
"""

import io
import time
import unittest
from unittest import mock

import server
import supervisor


class TestCleanFolderName(unittest.TestCase):
    clean = staticmethod(server.Handler._clean_folder_name)

    def test_title_with_year(self):
        self.assertEqual(self.clean("Inception (2010)"), "Inception 2010")

    def test_year_plus_quality_bracket(self):
        # The real library naming convention from the /add-movie skill.
        self.assertEqual(self.clean("Black Phone 2 (2025) [1080p]"), "Black Phone 2 2025")

    def test_scene_release_dotted(self):
        self.assertEqual(self.clean("Movie.1080p.BluRay.x264.AAC-GROUP"), "Movie")

    def test_dotted_name_without_parens(self):
        self.assertEqual(self.clean("Some.Dotted.Title.2019"), "Some Dotted Title 2019")

    def test_plain_title(self):
        self.assertEqual(self.clean("The Matrix"), "The Matrix")

    def test_bracket_tag_stripped(self):
        self.assertEqual(self.clean("Dune (2021) [2160p]"), "Dune 2021")


class TestOllamaModel(unittest.TestCase):
    def test_configured_model_wins(self):
        with mock.patch.object(server, "OLLAMA_MODEL", "llama3.2:3b"):
            # self is unused by the method, so None is fine.
            self.assertEqual(server.Handler._ollama_model(None), "llama3.2:3b")

    def test_autopick_skips_embedding(self):
        models = {"data": [{"id": "nomic-embed-text"}, {"id": "qwen2.5:7b"}]}
        fake = io.BytesIO(__import__("json").dumps(models).encode())
        with mock.patch.object(server, "OLLAMA_MODEL", ""), \
             mock.patch.object(server.urllib.request, "urlopen", return_value=fake):
            self.assertEqual(server.Handler._ollama_model(None), "qwen2.5:7b")

    def test_autopick_returns_none_on_error(self):
        with mock.patch.object(server, "OLLAMA_MODEL", ""), \
             mock.patch.object(server.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(server.Handler._ollama_model(None))


class TestSupervisor(unittest.TestCase):
    def test_registry_has_critical_core(self):
        by_name = {s.name: s for s in supervisor.SERVICES}
        for core in ("docker", "qbittorrent", "radarr"):
            self.assertIn(core, by_name)
            self.assertTrue(by_name[core].critical, f"{core} should be critical")

    def test_heal_unknown_service(self):
        r = supervisor.heal("does-not-exist")
        self.assertFalse(r["ok"])

    def test_status_starting_within_grace(self):
        svc = supervisor.SERVICES[0]
        supervisor._last_heal[svc.name] = time.time()          # just healed
        self.assertEqual(supervisor._status_of(svc, up=False), "starting")
        supervisor._last_heal[svc.name] = 0                     # long ago
        self.assertEqual(supervisor._status_of(svc, up=False), "down")
        self.assertEqual(supervisor._status_of(svc, up=True), "up")

    def test_new_completions_seed_then_detect(self):
        t1 = [{"hash": "a", "progress": 1.0, "name": "Done"}, {"hash": "b", "progress": 0.5}]
        new, done = supervisor._new_completions(None, t1)      # first run seeds silently
        self.assertEqual(new, [])
        self.assertEqual(done, {"a"})
        t2 = [{"hash": "a", "progress": 1.0}, {"hash": "b", "progress": 1.0, "name": "WIP"}]
        new, done = supervisor._new_completions(done, t2)      # b just finished
        self.assertEqual([t["hash"] for t in new], ["b"])
        self.assertEqual(done, {"a", "b"})
        new, done = supervisor._new_completions(done, t2)      # nothing new
        self.assertEqual(new, [])

    def test_overall_up_degraded_down(self):
        with mock.patch.object(supervisor, "_last_heal", {}):
            # all up
            for s in supervisor.SERVICES:
                s.probe = lambda: True
            self.assertEqual(supervisor.check_all(heal=False)["overall"], "up")
            # a non-critical down -> degraded
            by_name = {s.name: s for s in supervisor.SERVICES}
            by_name["sonarr"].probe = lambda: False
            self.assertEqual(supervisor.check_all(heal=False)["overall"], "degraded")
            # a critical down -> down
            by_name["radarr"].probe = lambda: False
            self.assertEqual(supervisor.check_all(heal=False)["overall"], "down")


if __name__ == "__main__":
    unittest.main(verbosity=2)
