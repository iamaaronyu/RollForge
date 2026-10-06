import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def recovery(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return runpy.run_path(str(scripts / "recover_e2b_host.py"))


def test_recovery_requires_owner_root_before_any_command(recovery, monkeypatch, tmp_path):
    apply = recovery["apply"]
    monkeypatch.setattr(apply.__globals__["os"], "geteuid", lambda: 1000)
    run = Mock()
    monkeypatch.setattr(apply.__globals__["subprocess"], "run", run)
    with pytest.raises(PermissionError):
        apply(tmp_path)
    run.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", [0, 2])
def test_active_or_unknown_vm_state_never_restarts_services(
    recovery, monkeypatch, tmp_path, status
):
    apply = recovery["apply"]
    monkeypatch.setattr(apply.__globals__["os"], "geteuid", lambda: 0)
    run = Mock(return_value=SimpleNamespace(returncode=status))
    monkeypatch.setattr(apply.__globals__["subprocess"], "run", run)
    with pytest.raises(RuntimeError):
        apply(tmp_path)
    assert run.call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_firewall_check_failure_never_starts_compose(recovery, monkeypatch, tmp_path):
    apply = recovery["apply"]
    monkeypatch.setattr(apply.__globals__["os"], "geteuid", lambda: 0)
    run = Mock(side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=2)])
    monkeypatch.setattr(apply.__globals__["subprocess"], "run", run)
    with pytest.raises(RuntimeError):
        apply(tmp_path)
    assert all(call.args[0][0] != "docker" for call in run.call_args_list)
    log = next(tmp_path.glob("*.local.log"))
    assert log.stat().st_mode & 0o777 == 0o600
