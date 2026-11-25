import unittest
from unittest.mock import Mock, patch

import server


class ModelCatalogEndpointTests(unittest.TestCase):
    def test_picker_catalog_does_not_wait_for_authentication_probes(self):
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/agent/models"
        handler._send_json = Mock()
        handler._api_auth_overview = Mock(side_effect=AssertionError("slow authentication overview"))
        catalogs = {"opencode": [{"id": "opencode-go/fixture", "name": "Fixture"}]}
        defaults = {"opencode": {"model": "opencode-go/fixture"}}
        with patch.object(server, "mission_model_catalogs", return_value=catalogs), \
                patch.object(server, "default_mission_models", return_value=defaults), \
                patch.object(server, "default_runtime", return_value="opencode"), \
                patch.object(server, "opencode_status", side_effect=AssertionError("auth probe")), \
                patch.object(server, "claude_auth_status", side_effect=AssertionError("auth probe")):
            handler.do_GET()
        self.assertEqual(handler._send_json.call_args.args[0], {
            "agent_runtime": "opencode", "agent_models": defaults, "agent_catalogs": catalogs})
        handler._api_auth_overview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
