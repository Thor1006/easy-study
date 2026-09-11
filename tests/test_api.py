"""Runtime host API: security checks, runs, steer/cap through validation, SSE, and the Silicate page."""

import http.client
import json
import time

import pytest

from glass_membrane.adapters.fake import FakeAdapter
from glass_membrane.api import Host
from glass_membrane.registry import Config
from glass_membrane.runtime import Runtime


@pytest.fixture
def host(tmp_path):
    runtime = Runtime(tmp_path / "data",
                      config=Config({"runtime": {"fsync": False, "watchdog_interval_s": 0.05}}),
                      adapters={p: FakeAdapter(p, delays={"performance": 0.4, "efficiency": 0.4})
                                for p in ("claude", "codex")},
                      demo=True)
    h = Host(runtime, state_file=tmp_path / "data" / "host.json").start()
    h.serve_in_thread()
    yield h
    h.close()


def request(host, method, path, body=None, token=True, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=15)
    hdrs = {"Host": f"127.0.0.1:{host.port}"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["X-GM-Token"] = host.token
    hdrs.update(headers or {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdrs)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.getheader("Content-Type", "").startswith("application/json"):
        return response.status, json.loads(data)
    return response.status, data


def wait_for(host, run_id, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, detail = request(host, "GET", f"/api/runs/{run_id}")
        if predicate(detail):
            return detail
        time.sleep(0.03)
    raise AssertionError("condition not reached")


def finished(detail):
    return detail["status"] in ("done", "failed", "cancelled")


def test_state_file_and_security_checks(host, tmp_path):
    status, state = request(host, "GET", "/api/state")
    assert status == 200 and state["demo"] is True
    assert len(state["slots"]["cores"]) == 12 and len(state["slots"]["filters"]) == 8
    assert json.loads((tmp_path / "data" / "host.json").read_text())["port"] == host.port
    assert request(host, "POST", "/api/runs", {"text": "2x2"}, token=False)[0] == 403
    assert request(host, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert request(host, "POST", "/api/runs", {"text": "2x2"}, headers={"Origin": "http://evil.example"})[0] == 403
    assert request(host, "POST", "/api/runs", {"text": "   "})[0] == 400
    assert request(host, "GET", "/static/../api.py")[0] == 404


def test_submit_and_filter_answer_through_the_api(host):
    status, body = request(host, "POST", "/api/runs", {"text": "2x2"})
    assert status == 201
    detail = wait_for(host, body["run"]["id"], finished)
    assert detail["answer"] == "4" and detail["answered_by"].startswith("filter")
    assert detail["task"] is None and detail["peak_processes"] == 1


def test_steer_and_cap_go_through_runtime_validation(host):
    _, body = request(host, "POST", "/api/runs", {"text": "Compare apples, oranges and pears"})
    run_id = body["run"]["id"]
    wait_for(host, run_id, lambda d: d["task"] and any(n["status"] == "running" for n in d["task"]["nodes"].values()))
    assert request(host, "POST", f"/api/runs/{run_id}/cap", {"n": 0})[0] == 400
    status, cap = request(host, "POST", f"/api/runs/{run_id}/cap", {"n": 2})
    assert status == 200 and cap["max_agents"] == 2
    status, summary = request(host, "POST", f"/api/runs/{run_id}/steer", {"text": "Keep it short"})
    assert status == 200 and summary["applied"] and summary["scope"] == "global"
    detail = wait_for(host, run_id, finished)
    assert detail["status"] == "done" and detail["task"]["version"] == 2
    assert "Keep it short" in detail["answer"]
    assert request(host, "POST", "/api/runs/run-missing/steer", {"text": "x"})[0] == 404


def test_event_stream_replays_backlog(host):
    _, body = request(host, "POST", "/api/runs", {"text": "2x2"})
    wait_for(host, body["run"]["id"], finished)
    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    conn.request("GET", "/api/events?since=0", headers={"Host": f"127.0.0.1:{host.port}"})
    response = conn.getresponse()
    assert response.status == 200 and "text/event-stream" in response.getheader("Content-Type")
    types = []
    while "run_done" not in types:
        line = response.fp.readline().decode("utf-8")
        if line.startswith("data: "):
            types.append(json.loads(line[6:])["type"])
    conn.close()
    assert types[0] == "run_submitted" and "filter_started" in types


def test_silicate_page_gets_token_and_strict_csp(host):
    conn = http.client.HTTPConnection("127.0.0.1", host.port, timeout=10)
    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{host.port}"})
    response = conn.getresponse()
    html = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200 and host.token in html and "{{GM_TOKEN}}" not in html
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    for asset in ("/static/app.js", "/static/styles.css"):
        assert request(host, "GET", asset)[0] == 200
