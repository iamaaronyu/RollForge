from uuid import uuid4

import pytest
from pydantic import ValidationError
from rollforge_schemas.domain import RevisionRef
from rollforge_schemas.execution import (
    ExecutionSnapshot,
    ExecutionStatus,
    ResultCommit,
    validate_execution_transition,
)


def test_snapshot_rejects_credentials_and_is_frozen():
    ref = RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64)
    with pytest.raises(ValidationError):
        ExecutionSnapshot(task=ref, agent=ref, model=ref, api_key="forbidden")
    snapshot = ExecutionSnapshot(task=ref, agent=ref, model=ref)
    with pytest.raises(ValidationError):
        snapshot.max_executions = 99


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "SCORED"},
        {"outcome": "SCORED", "rewards": {"reward": float("nan")}},
        {"outcome": "RUNTIME_FAILED", "rewards": {"reward": 0}, "failure_reason": "INFRA_ERROR"},
        {"outcome": "UNVERIFIED"},
    ],
)
def test_invalid_result_semantics(payload):
    with pytest.raises(ValidationError):
        ResultCommit(**payload, manifest_key="manifest.json", manifest_digest="sha256:" + "a" * 64)


def test_zero_score_is_valid_and_expired_execution_cannot_reopen():
    result = ResultCommit(
        outcome="SCORED",
        rewards={"reward": 0},
        manifest_key="manifest.json",
        manifest_digest="sha256:" + "a" * 64,
    )
    assert result.rewards == {"reward": 0}
    with pytest.raises(ValueError):
        validate_execution_transition(ExecutionStatus.EXPIRED, ExecutionStatus.RUNNING)
