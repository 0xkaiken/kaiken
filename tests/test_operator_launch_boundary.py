"""Privileged HTTP routes and background origin propagation, without live agents."""

import json
import threading
import urllib.error
import urllib.request
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import pytest

import account_guard
import server


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("PLINY_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(server, "AGENT_TOKEN", "agent-fixture")
    monkeypatch.setattr(server, "_operator_secret", lambda: "operator-fixture")
    for name in ("MISSIONS_FILE", "SCHEDULES_FILE"):
        path = tmp_path / name
        path.write_text('[{"id":"fixture","status":"queued","prompt":"fixture"}]')
        monkeypatch.setattr(server, name, path)
    with ExitStack() as stack:
        effects = {}
        for name in ("launch_lair_pack", "flight_launch", "launch_session",
                     "ouroboros_create_campaign"):
            effects[name] = stack.enter_context(patch.object(server, name, return_value={"id": "mock"}))
        gauntlet = Mock()
        gauntlet.launch_gauntlet.return_value = {"id": "mock"}
        monkeypatch.setattr(server, "gauntlet_mod", gauntlet)
        fixer = Mock()
        for method in ("start", "stop", "panic", "apply_staged_fix", "reject_staged_fix"):
            getattr(fixer.get_watchdog.return_value, method).return_value = {"ok": True}
        monkeypatch.setattr(server, "fixer_mod", fixer)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def request(method, path, token=None):
            body = {"mission": "fixture", "topic": "fixture", "goal": "fixture",
                    "preset_id": "fixture", "prompt": "fixture", "interval": "1h",
                    "mode": "off", "id": "fixture", "enabled": True}
            data = json.dumps([] if path == "/api/schedules" else body).encode()
            # A denied request must be rejected before body parsing. An empty
            # body also avoids racing Windows' close of an unread request body.
            if token != "operator-fixture":
                data = b""
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}{path}",
                data=data, headers=headers, method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    response.read()
                    return response.status
            except urllib.error.HTTPError as error:
                with error:
                    error.read()
                    return error.code

        try:
            yield request, effects
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def test_agent_and_anonymous_requests_cannot_launch_or_change_automation(api):
    request, effects = api
    routes = [
        ("POST", "/api/lair/launch-pack"), ("POST", "/api/flight/launch"),
        ("POST", "/api/gauntlet/launch"), ("POST", "/api/ouroboros/launch"),
        ("POST", "/api/missions/launch-next"), ("POST", "/api/schedule"),
        ("PUT", "/api/schedule"), ("PUT", "/api/schedules"),
        ("DELETE", "/api/schedule?id=fixture"),
        ("POST", "/api/watchdog/toggle"), ("POST", "/api/watchdog/panic"),
        ("POST", "/api/watchdog/apply/fixture"), ("POST", "/api/watchdog/reject/fixture"),
    ]
    for token in (None, "agent-fixture"):
        for method, path in routes:
            assert request(method, path, token) == 403, (method, path, token)
    for effect in effects.values():
        effect.assert_not_called()


def test_operator_can_still_launch_and_manage_schedules(api):
    request, effects = api
    done = threading.Event()
    seen = []

    def launch(*args, **kwargs):
        seen.append((account_guard.current_origin(), account_guard.current_authorization()))
        done.set()

    effects["launch_lair_pack"].side_effect = launch
    assert request("POST", "/api/lair/launch-pack", "operator-fixture") == 200
    assert done.wait(3)
    assert seen == [("operator", True)]
    assert request("POST", "/api/schedule", "operator-fixture") == 200
    assert request("POST", "/api/watchdog/toggle", "operator-fixture") == 200


@pytest.mark.parametrize("origin,authorized", [("agent", False), ("operator", True)])
def test_background_callback_inherits_origin(origin, authorized):
    seen = []
    with account_guard.origin_context(origin, authorized):
        callback = account_guard.bind_current_origin(
            lambda: seen.append((account_guard.current_origin(),
                                 account_guard.current_authorization()))
        )
    thread = threading.Thread(target=callback)
    thread.start()
    thread.join(timeout=3)
    assert seen == [(origin, authorized)]
