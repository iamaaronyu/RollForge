"""网关会话管理只操作合成密钥，并验证私密配置边界。"""

import hashlib
import json
import runpy
import stat
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest

manager = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/manage_gateway_sessions.py")
)
SessionConfigError = manager["SessionConfigError"]
cost_to_microusd = manager["cost_to_microusd"]
issue_session = manager["issue_session"]
main = manager["main"]
revoke_all = manager["revoke_all"]
revoke_session = manager["revoke_session"]


@pytest.fixture
def config(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    path = private / "gateway.json"
    path.write_text(json.dumps({"upstream_key": "synthetic-upstream-key", "sessions": {}}))
    path.chmod(0o600)
    return path


def test_issue_stores_only_hash_with_short_lived_limits(config):
    token = issue_session(config, 120, 5, 2_500_000)
    data = json.loads(config.read_text())
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    assert token_hash in data["sessions"]
    assert token not in config.read_text()
    assert data["sessions"][token_hash]["model"] == "deepseek-flash"
    assert data["sessions"][token_hash]["max_requests"] == 5
    assert data["sessions"][token_hash]["max_cost_microusd"] == 2_500_000
    assert data["sessions"][token_hash]["expires_at"] > 0
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.with_name(config.name + ".lock").stat().st_mode) == 0o600


def test_parallel_issuance_does_not_lose_sessions(config):
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: issue_session(config, 60, 2, 1_000_000), range(16)))

    data = json.loads(config.read_text())
    assert len(tokens) == len(set(tokens)) == 16
    assert len(data["sessions"]) == 16


def test_revoke_uses_token_hash_and_revoke_all_clears_sessions(config):
    first = issue_session(config, 60, 2, 1_000_000)
    second = issue_session(config, 60, 2, 1_000_000)

    assert revoke_session(config, first)
    assert not revoke_session(config, first)
    assert hashlib.sha256(second.encode()).hexdigest() in json.loads(config.read_text())["sessions"]
    assert revoke_all(config) == 1
    assert json.loads(config.read_text())["sessions"] == {}
    assert revoke_all(config) == 0


def test_cli_prints_token_once_and_accepts_revoke_from_stdin(config, monkeypatch, capsys):
    assert (
        main(
            [
                "issue",
                "--config",
                str(config),
                "--ttl-seconds",
                "60",
                "--max-requests",
                "1",
                "--max-cost-usd",
                "1.25",
            ]
        )
        == 0
    )
    token = capsys.readouterr().out.strip()
    assert token
    assert token not in config.read_text()

    monkeypatch.setattr(manager["sys"], "stdin", StringIO(token))
    assert main(["revoke", "--config", str(config)]) == 0
    assert capsys.readouterr().out.strip() == "会话已撤销"
    assert json.loads(config.read_text())["sessions"] == {}


def test_unsafe_permissions_and_symlink_config_are_rejected(config, tmp_path):
    config.chmod(0o640)
    with pytest.raises(SessionConfigError, match="权限 0600"):
        issue_session(config, 60, 1, 1_000_000)

    config.chmod(0o600)
    link = config.parent / "linked.json"
    link.symlink_to(config)
    with pytest.raises(SessionConfigError, match="普通文件"):
        issue_session(link, 60, 1, 1_000_000)

    config.parent.chmod(0o750)
    with pytest.raises(SessionConfigError, match="权限 0700"):
        revoke_all(config)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.000001", 1), ("2.5", 2_500_000), ("100", 100_000_000)],
)
def test_cost_input_converts_exactly_to_microusd(value, expected):
    assert cost_to_microusd(value) == expected


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "NaN", "Infinity", "0.0000001", "1e100000000", "not-a-price"],
)
def test_invalid_cost_inputs_are_rejected(value):
    with pytest.raises(SessionConfigError):
        cost_to_microusd(value)


@pytest.mark.parametrize(
    "ttl,requests,cost",
    [(0, 1, 1), (3601, 1, 1), (60, 0, 1), (60, 1, 0), (60, 1, 1_000_000_001)],
)
def test_invalid_session_limits_are_rejected(config, ttl, requests, cost):
    with pytest.raises(SessionConfigError):
        issue_session(config, ttl, requests, cost)


def test_boolean_session_limits_are_rejected(config):
    with pytest.raises(SessionConfigError):
        issue_session(config, True, 1, 1)
    with pytest.raises(SessionConfigError):
        issue_session(config, 60, True, 1)
    with pytest.raises(SessionConfigError):
        issue_session(config, 60, 1, True)
