import json

import pytest
from rollforge_harbor_adapter.results import summarize_result
from rollforge_schemas.runtime import ResultOutcome


def save(directory, **changes):
    payload = {
        "started_at": "2026-10-05T00:00:00Z",
        "finished_at": "2026-10-05T00:01:00Z",
        "agent_result": None,
        "verifier_result": None,
        "exception_info": None,
    }
    (directory / "result.json").write_text(json.dumps(payload | changes))


def test_zero_reward_is_valid_scored_result(tmp_path):
    save(tmp_path, verifier_result={"rewards": {"reward": 0}})
    summary = summarize_result(tmp_path)
    assert summary.outcome == ResultOutcome.SCORED
    assert summary.rewards == {"reward": 0}
    assert summary.input_tokens is None
    assert summary.trajectory_state == "MISSING"
    assert summary.timing["agent_execution"] is None
    assert summary.timing["total"] == 60


def test_missing_reward_is_not_zero(tmp_path):
    save(tmp_path)
    assert summarize_result(tmp_path).outcome == ResultOutcome.UNVERIFIED
    assert summarize_result(tmp_path).rewards is None


def test_exception_takes_precedence_over_partial_reward(tmp_path):
    save(
        tmp_path,
        exception_info={"exception_type": "VerifierError"},
        verifier_result={"rewards": {"reward": 1}},
    )
    summary = summarize_result(tmp_path)
    assert summary.outcome == ResultOutcome.RUNTIME_FAILED
    assert summary.exception_type == "VerifierError"


@pytest.mark.parametrize("reward", [float("nan"), float("inf"), True, "1"])
def test_invalid_reward_is_rejected(tmp_path, reward):
    save(tmp_path, verifier_result={"rewards": {"reward": reward}})
    with pytest.raises(ValueError, match="finite"):
        summarize_result(tmp_path)


def test_incomplete_result_cannot_be_accepted(tmp_path):
    save(tmp_path, finished_at=None)
    with pytest.raises(ValueError, match="not finalized"):
        summarize_result(tmp_path)


def test_negative_duration_is_rejected(tmp_path):
    save(tmp_path, finished_at="2026-10-04T00:00:00Z")
    with pytest.raises(ValueError, match="negative"):
        summarize_result(tmp_path)
