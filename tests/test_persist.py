import os
import unittest
from unittest.mock import patch

import persist


class PersistTests(unittest.TestCase):
    def test_local_does_not_implicitly_push(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaises(RuntimeError):
            persist.persist()

    def test_remote_advance_aborts_without_add_or_push(self):
        calls = []
        def git(*args):
            calls.append(args)
            if args == ("rev-parse", "HEAD"):
                return "local"
            if args == ("rev-parse", "FETCH_HEAD"):
                return "remote"
            if args[0] == "merge-base":
                raise RuntimeError("diverged")
            return ""
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main", "GITHUB_REF_TYPE": "branch"}), patch("persist.git", side_effect=git):
            with self.assertRaises(RuntimeError):
                persist.persist()
        self.assertFalse(any(c[0] in ("add", "commit", "push") for c in calls))

    def test_state_only_does_not_stage_other_files(self):
        calls = []
        def git(*args):
            calls.append(args)
            return "same" if args[0] == "rev-parse" else ""
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main", "GITHUB_REF_TYPE": "branch"}), patch("persist.git", side_effect=git):
            persist.persist(True)
        self.assertIn(("add", "--", "state.json"), calls)
        self.assertIn(("push", "--quiet", "origin", "HEAD:refs/heads/main"), calls)
