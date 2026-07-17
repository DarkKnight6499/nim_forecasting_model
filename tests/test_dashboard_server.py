"""
Acceptance tests for the live dashboard server (dashboard_server.py): starts
a real HTTP server on an ephemeral localhost port in a background thread,
exercises it with urllib, and shuts it down. Offline (no --bank-cert in these
tests, so no live FDIC/Treasury calls).
Run with: py -m pytest tests/ -q
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

import dashboard_server


@pytest.fixture(scope="module")
def server():
    dashboard_server._STARTUP_DEFAULTS["months"] = 6
    dashboard_server._STARTUP_DEFAULTS["bank_cert"] = None
    httpd = dashboard_server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(base_url, payload):
    req = urllib.request.Request(
        f"{base_url}/api/run", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# 1. GET / serves the live page with the inputs panel.
# ---------------------------------------------------------------------------

def test_get_root_serves_live_page_with_inputs_panel(server):
    with urllib.request.urlopen(f"{server}/", timeout=60) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
    assert "run-button" in html
    assert "table-position-overrides" in html


# ---------------------------------------------------------------------------
# 2. A position override actually reaches the engine (changes the result).
# ---------------------------------------------------------------------------

def test_position_override_changes_nim_result(server):
    status_plain, data_plain = _post(server, {"months": 6})
    assert status_plain == 200

    status_override, data_override = _post(server, {
        "months": 6,
        "position_overrides": {"Time deposits (CDs)": {"growth_rate_annual": 2.0, "rate": 0.20}},
    })
    assert status_override == 200

    base = data_plain["base_scenario"]
    plain_nim = [row["nim"] for row in data_plain["nim_by_scenario"][base]]
    override_nim = [row["nim"] for row in data_override["nim_by_scenario"][base]]
    assert plain_nim != override_nim


# ---------------------------------------------------------------------------
# 3. Bad input comes back as a clean 400 JSON error, not a 500/stack trace.
# ---------------------------------------------------------------------------

def test_invalid_override_field_returns_400_json_error(server):
    status, data = _post(server, {"position_overrides": {"Time deposits (CDs)": {"not_a_field": 1}}})
    assert status == 400
    assert "not_a_field" in data["error"]


def test_unknown_position_returns_400_json_error(server):
    status, data = _post(server, {"position_overrides": {"Not A Real Position": {"balance": 1}}})
    assert status == 400
    assert "Not A Real Position" in data["error"]


# ---------------------------------------------------------------------------
# 4. custom_shock_bps produces an extra scenario.
# ---------------------------------------------------------------------------

def test_custom_shock_bps_adds_a_scenario(server):
    status, data = _post(server, {"months": 6, "custom_shock_bps": 37})
    assert status == 200
    assert "Custom shock" in data["scenarios"]
