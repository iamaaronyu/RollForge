from rollforge_schemas.domain import TrialStatus

TRANSITIONS: dict[TrialStatus, frozenset[TrialStatus]] = {
    TrialStatus.PENDING: frozenset({TrialStatus.QUEUED, TrialStatus.CANCELLED}),
    TrialStatus.QUEUED: frozenset({TrialStatus.RUNNING, TrialStatus.CANCELLED}),
    TrialStatus.RUNNING: frozenset(
        {
            TrialStatus.QUEUED,
            TrialStatus.COMPLETED,
            TrialStatus.FAILED,
            TrialStatus.CANCELLED,
        }
    ),
    TrialStatus.COMPLETED: frozenset(),
    TrialStatus.FAILED: frozenset(),
    TrialStatus.CANCELLED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def validate_transition(current: TrialStatus, target: TrialStatus) -> None:
    """Allow idempotent updates; prevent terminal results from being reopened.

    RUNNING -> QUEUED is reserved for fenced infrastructure retry.
    Callers must enforce lease ownership and retry policy transactionally.
    """
    if current != target and target not in TRANSITIONS[current]:
        raise InvalidTransition(f"Invalid trial transition: {current} -> {target}")
