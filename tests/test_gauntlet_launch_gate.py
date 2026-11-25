"""The gauntlet launch gate: a wave may not start past a BROKEN preflight.

Covers the failed-close path, the degraded-but-alive path, the operator
override, and one live run of the real preflight script (shape only - the
verdict may legitimately be any of CLEAR/DEGRADED/BROKEN depending on what
is running when the test executes).
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import pytest

import server


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("PLINY_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(server, "AGENT_TOKEN", "agent-fixture")
    monkeypatch.setattr(server, "_operator_secret", lambda: "operator-fixture")
    gauntlet = Mock()
    gauntlet.launch_gauntlet.return_value = {"id": "mock-run"}
    monkeypatch.setattr(server, "gauntlet_mod", gauntlet)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(body, token="operator-fixture"):
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/gauntlet/launch",
            data=data, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def set_preflight(verdict, quoteable):
        payload = {"tool": "wave_preflight", "verdict": verdict,
                   "quoteable": quoteable,
                   "checks": [{"check": "harness", "ok": verdict != "BROKEN",
                               "detail": "fixture"}]}
        patcher = patch.object(server.Handler, "_run_wave_preflight",
                               return_value=payload)
        patcher.start()
        return patcher

    try:
        yield {"request": request, "gauntlet": gauntlet, "set_preflight": set_preflight}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_broken_preflight_refuses_the_launch(api):
    api["set_preflight"]("BROKEN", False)
    status, body = api["request"]({"preset_id": "fixture"})
    assert status == 503
    assert body["preflight"]["verdict"] == "BROKEN"
    assert "skip_preflight" in body["error"]
    assert "harness" in body["failing"]
    assert api["gauntlet"].launch_gauntlet.call_count == 0


def test_degraded_preflight_launches_but_the_scores_are_not_quoteable(api):
    api["set_preflight"]("DEGRADED", False)
    status, body = api["request"]({"preset_id": "fixture"})
    assert status == 200
    assert body["ok"] is True
    assert body["preflight"]["verdict"] == "DEGRADED"
    assert body["scores_quoteable"] is False
    assert api["gauntlet"].launch_gauntlet.call_count == 1


def test_clear_preflight_launches_and_the_scores_are_quoteable(api):
    api["set_preflight"]("CLEAR", True)
    status, body = api["request"]({"preset_id": "fixture"})
    assert status == 200
    assert body["preflight"]["verdict"] == "CLEAR"
    assert body["scores_quoteable"] is True
    assert api["gauntlet"].launch_gauntlet.call_count == 1


def test_operator_override_skip_preflight_launches_past_broken(api):
    api["set_preflight"]("BROKEN", False)
    status, body = api["request"]({"preset_id": "fixture", "skip_preflight": True})
    assert status == 200
    assert body["ok"] is True
    assert body["preflight"]["verdict"] == "BROKEN"
    assert body["scores_quoteable"] is False
    assert api["gauntlet"].launch_gauntlet.call_count == 1


def test_live_preflight_payload_shape():
    """The real script runs on this host. We assert the contract, not the verdict."""
    handler = server.Handler.__new__(server.Handler)
    payload = handler._run_wave_preflight(timeout=120)
    assert payload["tool"] == "wave_preflight"
    assert payload["verdict"] in ("CLEAR", "DEGRADED", "BROKEN")
    assert isinstance(payload.get("checks"), list)
    assert isinstance(payload.get("quoteable"), bool)
