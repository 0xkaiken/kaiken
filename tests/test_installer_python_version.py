"""Python minimum enforced before Windows setup performs any work."""

import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import install_windows


class InstallerPythonVersionTests(unittest.TestCase):
    def test_supported_versions_reach_setup(self):
        for version in ((3, 11), (3, 13)):
            with self.subTest(version=version), \
                    patch.object(sys, "version_info", version), \
                    patch.object(install_windows.importlib.util, "find_spec", return_value=object()), \
                    patch.object(install_windows, "initialize_files") as initialize, \
                    patch.object(install_windows, "install_optional_features"), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                install_windows.main([])
                initialize.assert_called_once()
                self.assertIn("Windows setup complete", output.getvalue())

    def test_older_versions_exit_before_setup(self):
        for version in ((3, 9), (3, 10)):
            with self.subTest(version=version), \
                    patch.object(sys, "version_info", version), \
                    patch.object(install_windows, "initialize_files") as initialize, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as exited:
                    install_windows.main([])
                self.assertEqual(exited.exception.code, 1)
                self.assertIn("Python 3.11 or newer is required.", error.getvalue())
                initialize.assert_not_called()
