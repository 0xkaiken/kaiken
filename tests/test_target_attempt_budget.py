import io
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import rt_send
import server


class TargetAttemptBudgetTests(unittest.TestCase):
    def request(self, **data):
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = lambda: json.dumps(data).encode()
        handler._send_json = Mock()
        handler._api_redteam_chat()
        return handler._send_json.call_args.args

    def test_real_sender_handshake_sends_twenty_calls_and_keeps_the_final_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = server.Session('fixture', 'Harmless task', runtime='codex', target={'max_attempts': 20})
            def dashboard(request, timeout):
                result = self.request(**json.loads(request.data))
                payload = json.dumps(result[0]).encode()
                if len(result) > 1:
                    raise HTTPError(request.full_url, result[1], 'Request denied', {}, io.BytesIO(payload))
                return io.BytesIO(payload)
            with patch.object(server, 'REDTEAM_CHATS_DIR', Path(tmp)), \
                    patch.object(server, 'active_sessions', {'fixture': session}), \
                    patch.object(server, 'broadcast_sse'), patch.object(server, 'rt_track_attempt'), \
                    patch.object(server, 'rt_log_attempt'), \
                    patch.object(server, 'rt_score_response', return_value={'outcome': 'blocked'}), \
                    patch.object(rt_send, '_PLINY_SESSION_ID', 'fixture'), \
                    patch.object(rt_send.urllib.request, 'urlopen', side_effect=dashboard), \
                    patch.object(rt_send, 'send_to_target', return_value='Harmless final answer') as send, \
                    patch('builtins.print'):
                for attempt in range(1, 22):
                    with patch.object(rt_send.sys, 'argv', ['rt_send.py', str(attempt), 'opencode', 'fixture']), \
                            patch.object(rt_send.sys, 'stdin', io.StringIO('Harmless fixture')):
                        if attempt <= 20:
                            rt_send.main()
                        else:
                            with self.assertRaises(SystemExit):
                                rt_send.main()
                self.assertEqual(send.call_count, 20)
                self.assertEqual(server.pending_target_attempts('fixture'), [])
                rows = [json.loads(line) for line in (Path(tmp) / 'fixture.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(rows[-1]['type'], 'response')
                self.assertEqual(rows[-1]['attempt'], 20)

    def test_workshop_probe_also_stops_before_target_call_when_denied(self):
        helper = Path(__file__).resolve().parents[1] / 'state/workshop/attempts/probe_send.py'
        if not helper.is_file():
            self.skipTest('Optional workshop helper is not installed')
        spec = importlib.util.spec_from_file_location('budget_probe_fixture', helper)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        with patch.object(rt_send, '_PLINY_SESSION_ID', 'fixture'), \
                patch.object(probe.sys, 'argv', ['probe_send.py', '21', 'opencode', 'fixture']), \
                patch.object(probe.sys, 'stdin', io.StringIO('Harmless fixture')), \
                patch.object(rt_send.urllib.request, 'urlopen', side_effect=HTTPError(
                    rt_send.DASHBOARD_API, 409, 'Limit reached', {}, io.BytesIO(b'Limit reached'))), \
                patch.object(probe, 'complete_text') as send:
            with self.assertRaises(SystemExit):
                probe.main()
            send.assert_not_called()

    def test_budget_ledger_write_failure_denies_the_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = server.Session('fixture', 'Harmless task', runtime='codex',
                                     target={'max_attempts': 5})
            with patch.object(server, 'REDTEAM_CHATS_DIR', Path(tmp)), \
                    patch.object(server, 'active_sessions', {'fixture': session}), \
                    patch.object(server.os, 'fsync',
                                 side_effect=OSError('disk full')):
                result = self.request(type='prompt', attempt=1,
                                      text='Harmless fixture',
                                      session_id='fixture', request_id='r1')
        self.assertFalse(result[0]['ok'])
        self.assertEqual(503, result[1])

    def test_twenty_requests_then_response_and_analysis_without_request_twenty_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = server.Session('fixture', 'Harmless task', runtime='codex',
                                     target={'max_attempts': 20})
            with patch.object(server, 'REDTEAM_CHATS_DIR', Path(tmp)), \
                    patch.object(server, 'active_sessions', {'fixture': session}), \
                    patch.object(server, 'broadcast_sse'), patch.object(server, 'rt_track_attempt'), \
                    patch.object(server, 'rt_log_attempt'), \
                    patch.object(server, 'rt_score_response', return_value={'outcome': 'blocked'}):
                for n in range(1, 21):
                    result = self.request(type='prompt', attempt=n, text='Harmless fixture',
                                          session_id='fixture', request_id=str(n))
                    self.assertTrue(result[0]['ok'])
                # A lost acknowledgment can retry without consuming another slot.
                retry = self.request(type='prompt', attempt=20, text='Harmless fixture',
                                     session_id='fixture', request_id='20')
                self.assertTrue(retry[0]['ok'])
                denied = self.request(type='prompt', attempt=21, text='Extra fixture',
                                      session_id='fixture', request_id='21')
                self.assertFalse(denied[0]['ok'])
                response = self.request(type='response', attempt=20, text='Harmless final answer', session_id='fixture')
                self.assertTrue(response[0]['ok'])
                session._target_completion_resumes = 1
                denied = self.request(type='prompt', attempt=1, text='Extra fixture',
                                      session_id='fixture', request_id='analysis')
                self.assertFalse(denied[0]['ok'])
                rows = [json.loads(line) for line in (Path(tmp) / 'fixture.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(sum(row['type'] == 'prompt' for row in rows), 20)
                self.assertEqual(server.pending_target_attempts('fixture'), list(range(1, 20)))
                self.assertEqual(session.status, 'running', 'Budget must not kill final response processing')

    def test_parallel_requests_and_reused_attempt_numbers_cannot_bypass_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = {sid: server.Session(sid, 'Harmless task', runtime='codex',
                        target={'max_attempts': limit}) for sid, limit in [('one', 20), ('two', 2)]}
            with patch.object(server, 'REDTEAM_CHATS_DIR', Path(tmp)), \
                    patch.object(server, 'active_sessions', sessions), \
                    patch.object(server, 'broadcast_sse'), patch.object(server, 'rt_track_attempt'):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda n: self.request(type='prompt', attempt=1,
                        text='Harmless fixture', session_id='one', request_id=str(n)), range(25)))
                self.assertEqual(sum(bool(result[0]['ok']) for result in results), 20)
                self.assertTrue(self.request(type='prompt', attempt=1, text='Other model fixture',
                                            session_id='two', request_id='independent')[0]['ok'])
                sessions['two']._target_completion_resumes = 1
                self.assertFalse(self.request(type='prompt', attempt=1, text='Analysis extra',
                                             session_id='two', request_id='extra')[0]['ok'])

    def test_continuations_and_restored_sessions_share_the_original_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = server.Session('root', 'Harmless task', runtime='codex', target={'max_attempts': 2})
            child = server.Session('child', 'Harmless task', runtime='codex', target=root.target)
            sessions = {'root': root, 'child': child}
            state = dict(active=True, session_id='child', auto_continue=True, aborted=False,
                         attempts_seen=1, max_attempts=2, retries_used=0, max_retries=5,
                         original_prompt='Harmless task', provider='fixture', model='fixture',
                         objective='Harmless task', target_budget=root.target)
            with patch.object(server, 'REDTEAM_CHATS_DIR', Path(tmp)), \
                    patch.object(server, 'active_sessions', sessions), \
                    patch.object(server, 'broadcast_sse'), patch.object(server, 'rt_track_attempt'), \
                    patch.object(server, '_rt_state', state):
                for sid in ('root', 'child'):
                    self.assertTrue(self.request(type='prompt', attempt=1, text='Harmless fixture',
                                                session_id=sid, request_id=sid)[0]['ok'])
                sessions['child'] = server.Session('child', 'Harmless task', runtime='codex', target=child.target)
                self.assertFalse(self.request(type='prompt', attempt=1, text='Extra fixture',
                                             session_id='child', request_id='restored')[0]['ok'])
                with patch.object(server, 'launch_session') as launch:
                    server.rt_session_ended('child')
                launch.assert_not_called()
                self.assertFalse(state['active'])

    def test_sender_must_not_call_target_when_server_denies_or_is_unavailable(self):
        for error in (HTTPError(rt_send.DASHBOARD_API, 429, 'Budget exhausted', {},
                               io.BytesIO(b'{"error":"Attempt limit reached"}')), OSError('Offline')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(rt_send, '_PLINY_SESSION_ID', 'fixture'), \
                    patch.object(rt_send.sys, 'argv', ['rt_send.py', '21', 'opencode', 'fixture']), \
                    patch.object(rt_send.sys, 'stdin', io.StringIO('Harmless fixture')), \
                    patch.object(rt_send.urllib.request, 'urlopen', side_effect=error), \
                    patch.object(rt_send, 'send_to_target') as send, patch('time.sleep'), \
                    patch.object(rt_send.sys, 'stderr', io.StringIO()):
                with self.assertRaises(SystemExit):
                    rt_send.main()
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
