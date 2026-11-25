import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class SecretsDestinationGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        secrets_file = Path(self.tmp.name) / "secrets.json"
        patches = [
            patch.object(server, "SECRETS_FILE", secrets_file),
            patch.object(server, "_sync_github_from_secrets"),
            patch.object(server, "_sync_env_from_secrets"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_masked_echo_does_not_overwrite_secret(self):
        self.assertTrue(server.secrets_update({"github_pat": "ghp_abc123def"})["ok"])
        masked = server.secrets_get_masked()["github_pat"]["value"]
        self.assertTrue(masked.startswith("****"))
        server.secrets_update({"github_pat": masked})
        self.assertEqual("ghp_abc123def", server._load_secrets()["github_pat"])

    def test_repo_change_without_credential_is_rejected(self):
        server.secrets_update({"github_pat": "ghp_abc123def",
                               "github_repo": "https://github.com/org/old"})
        masked = server.secrets_get_masked()["github_pat"]["value"]
        result = server.secrets_update({
            "github_repo": "https://github.com/attacker/new",
            "github_pat": masked,
        })
        self.assertFalse(result["ok"])
        self.assertEqual("destination_changed_without_credentials", result["code"])
        secrets = server._load_secrets()
        self.assertEqual("https://github.com/org/old", secrets["github_repo"])
        self.assertEqual("ghp_abc123def", secrets["github_pat"])

    def test_repo_change_with_fresh_credential_is_allowed(self):
        server.secrets_update({"github_pat": "ghp_old123",
                               "github_repo": "https://github.com/org/old"})
        result = server.secrets_update({
            "github_repo": "https://github.com/org/new",
            "github_pat": "ghp_new456",
        })
        self.assertTrue(result["ok"])
        secrets = server._load_secrets()
        self.assertEqual("https://github.com/org/new", secrets["github_repo"])
        self.assertEqual("ghp_new456", secrets["github_pat"])

    def test_repo_change_with_no_stored_credential_is_allowed(self):
        server.secrets_update({"github_repo": "https://github.com/org/old"})
        result = server.secrets_update({"github_repo": "https://github.com/org/new"})
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
