import hashlib
import io
import json
import runpy
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

gateway = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/model_gateway.py"))
Sessions = gateway["Sessions"]
UsageObserver = gateway["UsageObserver"]
request_cost_reservation = gateway["request_cost_reservation"]
cost_microusd = gateway["cost_microusd"]
SESSION_HASH = hashlib.sha256(b"synthetic-session").hexdigest()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "upstream_key": "synthetic-upstream-key",
                "sessions": {
                    SESSION_HASH: {
                        "model": "deepseek-flash",
                        "expires_at": time.time() + 60,
                        "max_requests": 3,
                        "max_cost_microusd": 10_000_000,
                    }
                },
            }
        )
    )
    path.chmod(0o600)
    return path


def test_unknown_session_and_wrong_model_never_authorized(config):
    sessions = Sessions(config)
    assert sessions.authorize("unknown", "deepseek-flash", 1) is None
    assert sessions.authorize("synthetic-session", "other-model", 1) is None
    assert (
        sessions.authorize("synthetic-session", "deepseek-flash", 1).upstream_key
        == "synthetic-upstream-key"
    )


def test_expired_session_rejected(config):
    data = json.loads(config.read_text())
    data["sessions"][SESSION_HASH]["expires_at"] = time.time() - 1
    config.write_text(json.dumps(data))
    assert Sessions(config).authorize("synthetic-session", "deepseek-flash", 1) is None
    assert not config.with_suffix(".sqlite3").exists()


def test_gateway_initializes_persistent_budget_ledger_before_first_request(config):
    sessions = Sessions(config)

    sessions.initialize()

    state_db = config.with_suffix(".sqlite3")
    assert state_db.is_file()
    assert state_db.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(state_db) as connection:
        assert connection.execute("SELECT count(*) FROM session_usage").fetchone() == (0,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_usage)")}
    assert {"used_cost_microusd", "reserved_cost_microusd"}.issubset(columns)


def test_gateway_migrates_old_request_only_ledger_without_resetting_requests(config):
    state_db = config.with_suffix(".sqlite3")
    with sqlite3.connect(state_db) as connection:
        connection.execute(
            "CREATE TABLE session_usage (token_sha256 TEXT PRIMARY KEY, "
            "used_requests INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO session_usage(token_sha256, used_requests) VALUES (?, 2)",
            (SESSION_HASH,),
        )
    state_db.chmod(0o600)

    Sessions(config).initialize()

    with sqlite3.connect(state_db) as connection:
        assert connection.execute(
            "SELECT used_requests, used_cost_microusd, reserved_cost_microusd "
            "FROM session_usage WHERE token_sha256 = ?",
            (SESSION_HASH,),
        ).fetchone() == (2, 0, 0)


def test_request_budget_is_atomic_under_concurrency(config):
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(
                lambda _: Sessions(config).authorize("synthetic-session", "deepseek-flash", 1),
                range(20),
            )
        )
    assert sum(value is not None for value in results) == 3


def test_request_budget_survives_gateway_restart_and_state_is_private(config):
    for _ in range(2):
        assert Sessions(config).authorize("synthetic-session", "deepseek-flash", 1)
    restarted = Sessions(config)
    assert restarted.authorize("synthetic-session", "deepseek-flash", 1)
    assert restarted.authorize("synthetic-session", "deepseek-flash", 1) is None
    state_db = config.with_suffix(".sqlite3")
    assert state_db.stat().st_mode & 0o777 == 0o600
    assert "synthetic-session" not in config.read_text()


def test_revocation_is_seen_without_restart(config):
    sessions = Sessions(config)
    assert sessions.authorize("synthetic-session", "deepseek-flash", 1)
    data = json.loads(config.read_text())
    data["sessions"] = {}
    config.write_text(json.dumps(data))
    assert sessions.authorize("synthetic-session", "deepseek-flash", 1) is None
    with sqlite3.connect(config.with_suffix(".sqlite3")) as connection:
        assert connection.execute(
            "SELECT used_requests FROM session_usage WHERE token_sha256 = ?", (SESSION_HASH,)
        ).fetchone() == (1,)


def test_shared_readable_config_rejected(config):
    config.chmod(0o644)
    with pytest.raises(ValueError):
        Sessions(config).authorize("synthetic-session", "deepseek-flash", 1)


def test_claude_beta_endpoint_keeps_fixed_upstream_and_substitutes_key(config, monkeypatch):
    handler = gateway["make_handler"]
    upstream = MagicMock()
    upstream.__enter__.return_value = upstream
    upstream.status = 200
    upstream.headers = {"Content-Type": "application/json"}
    upstream.read1.side_effect = io.BytesIO(
        b'{"type":"message","usage":{"input_tokens":20,"output_tokens":2}}'
    ).read1
    opener = MagicMock()
    opener.open.return_value = upstream
    monkeypatch.setitem(handler.__globals__, "build_opener", lambda *_args: opener)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Sessions(config)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection(*server.server_address, timeout=5)
        body = json.dumps({"model": "deepseek-flash", "max_tokens": 10})
        connection.request(
            "POST", "/v1/messages?beta=true", body, {"x-api-key": "synthetic-session"}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {
            "type": "message",
            "usage": {"input_tokens": 20, "output_tokens": 2},
        }
        request = opener.open.call_args.args[0]
        assert request.full_url == "https://api.deepseek.com/anthropic/v1/messages"
        assert request.get_header("X-api-key") == "synthetic-upstream-key"
        with sqlite3.connect(config.with_suffix(".sqlite3")) as connection:
            assert connection.execute(
                "SELECT used_requests, used_cost_microusd, reserved_cost_microusd "
                "FROM session_usage WHERE token_sha256 = ?",
                (SESSION_HASH,),
            ).fetchone() == (1, cost_microusd(20, 2), 0)
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cost_reservation_uses_peak_rates_and_full_context_bound():
    reservation = request_cost_reservation({"max_tokens": 100})

    assert reservation == cost_microusd(2_000_000, 100)


def test_search_tools_reserve_separate_fee_and_unknown_server_tools_fail():
    reservation = request_cost_reservation(
        {"max_tokens": 100, "tools": [{"type": "web_search_20250305"}]}
    )

    assert reservation == cost_microusd(2_000_000, 100, 100)
    with pytest.raises(ValueError, match="unsupported billable"):
        request_cost_reservation({"max_tokens": 100, "tools": [{"type": "server_unknown"}]})


def test_stream_usage_is_parsed_without_buffering_response_content():
    observer = UsageObserver("text/event-stream")
    observer.feed(
        b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
        b'{"input_tokens":100,"cache_read_input_tokens":10}}}\n\n'
    )
    observer.feed(
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":20}}\n\n'
    )
    observer.feed(
        b'event: content_block_start\ndata: {"type":"content_block_start",'
        b'"content_block":{"type":"server_tool_use","name":"web_search"}}\n\n'
    )
    observer.feed(b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    assert observer.finish() == cost_microusd(110, 20, 1)


def test_incomplete_usage_keeps_maximum_reservation():
    observer = UsageObserver("text/event-stream")
    observer.feed(b'event: message_start\ndata: {"type":"message_start"}\n\n')

    assert observer.finish() is None


def test_cost_cap_is_atomic_and_settlement_releases_reservation(config):
    data = json.loads(config.read_text())
    data["sessions"][SESSION_HASH]["max_cost_microusd"] = 500
    config.write_text(json.dumps(data))
    sessions = Sessions(config)

    authorization = sessions.authorize("synthetic-session", "deepseek-flash", 400)
    assert authorization
    assert sessions.authorize("synthetic-session", "deepseek-flash", 101) is None
    sessions.settle(authorization, 100)
    assert sessions.authorize("synthetic-session", "deepseek-flash", 400)

    with sqlite3.connect(config.with_suffix(".sqlite3")) as connection:
        usage = connection.execute(
            "SELECT used_requests, used_cost_microusd, reserved_cost_microusd "
            "FROM session_usage WHERE token_sha256 = ?",
            (SESSION_HASH,),
        ).fetchone()
    assert usage == (2, 100, 400)


def test_concurrent_cost_reservations_never_exceed_session_cap(config):
    data = json.loads(config.read_text())
    data["sessions"][SESSION_HASH]["max_cost_microusd"] = 1000
    config.write_text(json.dumps(data))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: Sessions(config).authorize("synthetic-session", "deepseek-flash", 400),
                range(8),
            )
        )

    assert sum(authorization is not None for authorization in results) == 2
    with sqlite3.connect(config.with_suffix(".sqlite3")) as connection:
        assert connection.execute(
            "SELECT used_cost_microusd, reserved_cost_microusd "
            "FROM session_usage WHERE token_sha256 = ?",
            (SESSION_HASH,),
        ).fetchone() == (0, 800)


def test_sessions_without_cost_limit_fail_closed(config):
    data = json.loads(config.read_text())
    del data["sessions"][SESSION_HASH]["max_cost_microusd"]
    config.write_text(json.dumps(data))

    assert Sessions(config).authorize("synthetic-session", "deepseek-flash", 1) is None
