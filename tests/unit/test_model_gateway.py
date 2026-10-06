import io
import json
import runpy
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


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "upstream_key": "synthetic-upstream-key",
                "sessions": {
                    "synthetic-session": {
                        "model": "deepseek-flash",
                        "expires_at": time.time() + 60,
                        "max_requests": 3,
                    }
                },
            }
        )
    )
    path.chmod(0o600)
    return path


def test_unknown_session_and_wrong_model_never_authorized(config):
    sessions = Sessions(config)
    assert sessions.authorize("unknown", "deepseek-flash") is None
    assert sessions.authorize("synthetic-session", "other-model") is None
    assert sessions.authorize("synthetic-session", "deepseek-flash") == "synthetic-upstream-key"


def test_expired_session_rejected(config):
    data = json.loads(config.read_text())
    data["sessions"]["synthetic-session"]["expires_at"] = time.time() - 1
    config.write_text(json.dumps(data))
    assert Sessions(config).authorize("synthetic-session", "deepseek-flash") is None


def test_request_budget_is_atomic_under_concurrency(config):
    sessions = Sessions(config)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(lambda _: sessions.authorize("synthetic-session", "deepseek-flash"), range(20))
        )
    assert sum(value is not None for value in results) == 3


def test_revocation_is_seen_without_restart(config):
    sessions = Sessions(config)
    assert sessions.authorize("synthetic-session", "deepseek-flash")
    data = json.loads(config.read_text())
    data["sessions"] = {}
    config.write_text(json.dumps(data))
    assert sessions.authorize("synthetic-session", "deepseek-flash") is None


def test_shared_readable_config_rejected(config):
    config.chmod(0o644)
    with pytest.raises(ValueError):
        Sessions(config).authorize("synthetic-session", "deepseek-flash")


def test_claude_beta_endpoint_keeps_fixed_upstream_and_substitutes_key(config, monkeypatch):
    handler = gateway["make_handler"]
    upstream = MagicMock()
    upstream.__enter__.return_value = upstream
    upstream.status = 200
    upstream.headers = {"Content-Type": "application/json"}
    upstream.read1.side_effect = io.BytesIO(b'{"type":"message"}').read1
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
        assert json.loads(response.read()) == {"type": "message"}
        request = opener.open.call_args.args[0]
        assert request.full_url == "https://api.deepseek.com/anthropic/v1/messages"
        assert request.get_header("X-api-key") == "synthetic-upstream-key"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
