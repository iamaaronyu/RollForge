import pytest
from rollforge_schemas.domain import TrialStatus
from rollforge_schemas.state_machine import InvalidTransition, validate_transition


@pytest.mark.parametrize(
    "terminal", [TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.CANCELLED]
)
@pytest.mark.parametrize("target", [TrialStatus.PENDING, TrialStatus.QUEUED, TrialStatus.RUNNING])
def test_terminal_results_cannot_reopen(terminal, target):
    with pytest.raises(InvalidTransition):
        validate_transition(terminal, target)


@pytest.mark.parametrize("status", list(TrialStatus))
def test_status_updates_are_idempotent(status):
    validate_transition(status, status)


def test_cannot_complete_without_running():
    with pytest.raises(InvalidTransition):
        validate_transition(TrialStatus.QUEUED, TrialStatus.COMPLETED)


def test_infrastructure_retry_can_requeue_running_trial():
    validate_transition(TrialStatus.RUNNING, TrialStatus.QUEUED)
