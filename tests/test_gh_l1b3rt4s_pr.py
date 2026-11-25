import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import _paths, gh_l1b3rt4s_pr


class HomeTests(unittest.TestCase):
    def test_environment_override_without_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PLINY_HOME": tmp}), \
                    patch.dict(sys.modules, {"_paths": None}):
                self.assertEqual(gh_l1b3rt4s_pr.home(), Path(tmp).resolve())

    def test_importable_paths_without_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / "installed"
            with patch.dict(os.environ), \
                    patch.dict(sys.modules, {"_paths": _paths}), \
                    patch.object(_paths, "pliny_home", return_value=expected) as resolver:
                os.environ.pop("PLINY_HOME", None)
                self.assertEqual(gh_l1b3rt4s_pr.home(), expected)
                resolver.assert_called_once_with()

    def test_repo_fallback_without_paths_or_environment(self):
        with patch.dict(os.environ), patch.dict(sys.modules, {"_paths": None}):
            os.environ.pop("PLINY_HOME", None)
            self.assertEqual(
                gh_l1b3rt4s_pr.home(), Path(gh_l1b3rt4s_pr.__file__).resolve().parents[1]
            )
