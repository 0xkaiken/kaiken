import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_settings import (claude_auth_status, clear_claude_auth_cache, default_mission_models,
                            save_agent_preferences, configured_model, default_runtime, validate_model)


class AgentSettingsTests(unittest.TestCase):
    def tearDown(self):
        clear_claude_auth_cache()

    @patch('agent_settings.shutil.which', return_value='claude.exe')
    @patch('agent_settings.subprocess.run')
    def test_claude_status_exposes_only_account_display_fields(self, run, _which):
        run.return_value = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            'loggedIn': True, 'email': 'test@example.com', 'subscriptionType': 'pro',
            'authMethod': 'claude.ai', 'accessToken': 'do-not-expose', 'orgId': 'private-org',
        }))
        clear_claude_auth_cache()
        status = claude_auth_status()
        self.assertTrue(status['logged_in'])
        self.assertEqual(status['subscription_type'], 'pro')
        self.assertEqual(status['email'], 'test@example.com')
        self.assertNotIn('accessToken', status)
        self.assertNotIn('orgId', status)
        self.assertNotIn('do-not-expose', json.dumps(status))

    @patch('agent_settings.shutil.which', return_value=None)
    def test_missing_claude_is_not_reported_as_signed_in(self, _which):
        clear_claude_auth_cache()
        status = claude_auth_status()
        self.assertFalse(status['installed'])
        self.assertFalse(status['logged_in'])

    def test_codex_model_uses_config_with_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
            with patch.dict(os.environ, {'CODEX_HOME': tmp, 'PLINY_CODEX_MODEL': ''}):
                self.assertEqual(default_mission_models()['codex']['model'], 'gpt-test')
                os.environ['PLINY_CODEX_MODEL'] = 'gpt-override'
                model = default_mission_models()['codex']
                self.assertEqual(model['model'], 'gpt-override')
                self.assertEqual(model['source'], 'PLINY_CODEX_MODEL')

    def test_app_default_and_models_persist_without_keys(self):
        with tempfile.TemporaryDirectory() as tmp, patch('agent_settings.PREFERENCES_FILE', Path(tmp) / 'agent.json'):
            save_agent_preferences('opencode', 'opencode-go/glm-5.3-flash')
            self.assertEqual(default_runtime('claude'), 'opencode')
            self.assertEqual(configured_model('opencode'), 'opencode-go/glm-5.3-flash')
            self.assertEqual(default_mission_models()['opencode']['model'], 'opencode-go/glm-5.3-flash')
            self.assertEqual(set(json.loads((Path(tmp) / 'agent.json').read_text())), {'runtime', 'models'})

    def test_any_opencode_provider_but_no_cli_flags(self):
        # The operator connected OpenRouter/OrcaRouter keys in OpenCode; all providers are selectable.
        self.assertEqual(validate_model('opencode', 'openrouter/anthropic/claude-x'), 'openrouter/anthropic/claude-x')
        self.assertEqual(validate_model('opencode', 'opencode/some-zen-model'), 'opencode/some-zen-model')
        for bad in ('--some-option', 'no-provider', 'a b/c'):
            with self.assertRaises(ValueError):
                validate_model('opencode', bad)
        with self.assertRaises(ValueError):
            validate_model('codex', '--some-option')


if __name__ == '__main__':
    unittest.main()
