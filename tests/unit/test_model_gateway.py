import json
import runpy
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

Sessions = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/model_gateway.py"))[
    "Sessions"
]


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
