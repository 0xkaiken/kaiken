"""Exercise real state handlers against temporary files; never start an agent."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

import server


class Handler:
    def __init__(self, payload):
        self.payload = payload
        self.status = None
        self.response = None

    def _read_body(self):
        return json.dumps(self.payload).encode()

    def _send_json(self, response, status=200):
        self.response, self.status = response, status


@pytest.fixture
def state(tmp_path, monkeypatch):
    for name in ("MISSIONS_FILE", "SCHEDULES_FILE"):
        path = tmp_path / name
        path.write_text("[]")
        monkeypatch.setattr(server, name, path)
    monkeypatch.setattr(server, "_require_auth", lambda handler: True)
    monkeypatch.setattr(server, "broadcast_sse", lambda *args: None)
    return tmp_path


@pytest.mark.parametrize("kind", ["mission", "schedule"])
def test_concurrent_creates_keep_both_records(state, monkeypatch, kind):
    path = server.MISSIONS_FILE if kind == "mission" else server.SCHEDULES_FILE
    method = (server.Handler._api_missions_save if kind == "mission"
              else server.Handler._api_schedule_create)
    original_load = server.load_json
    barrier = threading.Barrier(2)

    def overlapping_read(file):
        value = original_load(file)
        if file == path:
            try:
                barrier.wait(timeout=0.3)
            except threading.BrokenBarrierError:
                pass
        return value

    monkeypatch.setattr(server, "load_json", overlapping_read)

    def create(number):
        handler = Handler({"id": str(number), "prompt": str(number), "interval": "1h"})
        method(handler)
        assert handler.status == 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(create, range(2)))
    assert len(json.loads(path.read_text())) == 2


def test_scheduler_does_not_overwrite_a_concurrent_schedule_create(state, monkeypatch):
    server.save_json(server.SCHEDULES_FILE, [{"id": "scheduled", "prompt": "fixture",
                                            "type": "interval", "interval_seconds": 60}])
    read, release, created = threading.Event(), threading.Event(), threading.Event()
    stop = threading.Event()

    def resolve(prompt):
        read.set()
        assert release.wait(3)
        return prompt

    def launch(*args, **kwargs):
        stop.set()
        return {"id": "mock"}

    def create():
        server.Handler._api_schedule_create(Handler({"prompt": "second", "interval": "1h"}))
        created.set()

    monkeypatch.setattr(server, "scheduler_stop_event", stop)
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(server, "_resolve_schedule_prompt", resolve)
    monkeypatch.setattr(server, "schedule_has_running_session", lambda sid: False)
    monkeypatch.setattr(server, "launch_session", launch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduler = pool.submit(server.scheduler_loop)
        try:
            assert read.wait(3)
            writer = pool.submit(create)
            created.wait(0.3)
        finally:
            release.set()
        scheduler.result(timeout=3)
        writer.result(timeout=3)
    rows = server.load_json(server.SCHEDULES_FILE)
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == "scheduled")["last_session_id"] == "mock"


def queued_mission():
    server.save_json(server.MISSIONS_FILE, [{"id": "m1", "status": "queued", "prompt": "fixture"}])


def test_failed_launch_returns_mission_to_queue(state, monkeypatch):
    queued_mission()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture startup failure")

    monkeypatch.setattr(server, "launch_session", fail)
    handler = Handler({})
    server.Handler._api_missions_launch_next(handler)
    mission = server.load_json(server.MISSIONS_FILE)[0]
    assert handler.status == 500
    assert mission["status"] == "queued"
    assert "fixture startup failure" in mission["last_launch_error"]


@pytest.mark.parametrize("resolved", [False, True])
def test_launch_completion_preserves_edits_made_while_starting(state, monkeypatch, resolved):
    queued_mission()

    def launch(*args, **kwargs):
        rows = server.load_json(server.MISSIONS_FILE)
        rows[0]["title"] = "edited during launch"
        if resolved:
            rows[0]["status"] = "resolved"
        server.save_json(server.MISSIONS_FILE, rows)
        return {"id": kwargs.get("session_id", "fixture"), "status": "running"}

    monkeypatch.setattr(server, "launch_session", launch)
    handler = Handler({})
    server.Handler._api_missions_launch_next(handler)
    mission = server.load_json(server.MISSIONS_FILE)[0]
    assert handler.status == 200
    assert mission["title"] == "edited during launch"
    assert mission["status"] == ("resolved" if resolved else "launched")


def test_background_startup_failure_requeues_mission(state):
    server.save_json(server.MISSIONS_FILE, [{"id": "m1", "status": "launched",
                                           "session_id": "startup", "prompt": "fixture"}])
    session = server.Session("startup", "fixture", runtime="opencode")
    session.mission_id = "m1"
    with patch.object(session, "_start_inner", side_effect=RuntimeError("fixture startup failure")), \
         patch.object(session, "_save_log"), patch.object(session, "_save_session_record"):
        session.start()
    assert server.load_json(server.MISSIONS_FILE)[0]["status"] == "queued"


def test_concurrent_launch_next_does_not_launch_same_mission_twice(state, monkeypatch):
    queued_mission()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def launch(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(3)
        return {"id": kwargs.get("session_id", "fixture"), "status": "running"}

    monkeypatch.setattr(server, "launch_session", launch)
    first, second = Handler({}), Handler({})
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(server.Handler._api_missions_launch_next, first)
        try:
            assert entered.wait(3)
            server.Handler._api_missions_launch_next(second)
        finally:
            release.set()
        pending.result(timeout=3)
    assert second.status == 404
    assert len(calls) == 1
