"""Synthetic evidence checks for the explicit real multi-Trial acceptance runner."""

import runpy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rollforge_schemas.domain import RevisionRef, TrialStatus
from rollforge_schemas.execution import (
    ExecutionList,
    ExecutionSnapshot,
    ExecutionStatus,
    ExecutionView,
    JobView,
    RegistrySelection,
    ResultCommit,
    TrialList,
    TrialView,
)
from rollforge_schemas.runnable import RunnableBinding
from rollforge_schemas.runtime import ResultOutcome

ROOT = Path(__file__).resolve().parents[2]
evaluate = runpy.run_path(str(ROOT / "examples/run_multitrial_acceptance.py"))["evaluate"]
safe_api_url = runpy.run_path(str(ROOT / "examples/run_multitrial_acceptance.py"))["safe_api_url"]


def evidence(*, duplicate_manifest=False, failed_trial=False):
    job_id = uuid4()
    refs = [
        RevisionRef(id=uuid4(), revision=1, digest="sha256:" + str(index) * 64)
        for index in range(3)
    ]
    selection = RegistrySelection(task=refs[0], agent=refs[1], model=refs[2])
    runtime = RunnableBinding(
        task_archive_key="tasks/accepted.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.example",
    )
    snapshot = ExecutionSnapshot(
        task=refs[0],
        agent=RevisionRef(id=refs[1].id, revision=1, digest=runtime.agent_digest),
        model=RevisionRef(id=refs[2].id, revision=1, digest=runtime.model_digest),
        runtime=runtime,
        registry=selection,
    )
    trials = []
    executions = []
    for index in range(2):
        result = ResultCommit(
            outcome=ResultOutcome.SCORED,
            rewards={"reward": 1.0},
            manifest_key="accepted/one.json" if duplicate_manifest else f"accepted/{index}.json",
            manifest_digest="sha256:" + "b" * 64,
        )
        trial_id = uuid4()
        trials.append(
            TrialView(
                trial_id=trial_id,
                job_id=job_id,
                status=TrialStatus.FAILED if failed_trial and index == 1 else TrialStatus.COMPLETED,
                attempt_index=index,
                fencing_token=1,
                result=result,
            )
        )
        executions.append(
            ExecutionView(
                execution_id=uuid4(),
                execution_sequence=index + 1,
                attempt_index=index,
                fencing_token=1,
                status=ExecutionStatus.COMPLETED,
                expires_at=datetime.now(UTC),
                result=result,
            )
        )
    job = JobView(
        job_id=job_id,
        owner_id=uuid4(),
        snapshot=snapshot,
        trial=trials[0],
        trial_count=2,
        terminal_trial_count=2,
    )
    return job, TrialList(items=tuple(trials)), ExecutionList(items=tuple(executions))


def test_multitrial_acceptance_requires_independent_scored_outputs():
    job, trials, executions = evidence()
    assert evaluate(job, trials, executions, job.snapshot.registry)["passed"] is True
    job, trials, executions = evidence(duplicate_manifest=True)
    assert evaluate(job, trials, executions, job.snapshot.registry)["passed"] is False
    job, trials, executions = evidence(failed_trial=True)
    assert evaluate(job, trials, executions, job.snapshot.registry)["passed"] is False


def test_acceptance_sdk_token_requires_tls_or_loopback_http():
    assert safe_api_url("https://hub.example")
    assert safe_api_url("http://127.0.0.1:8000")
    assert safe_api_url("http://[::1]:8000")
    assert not safe_api_url("http://100.106.244.122:8000")
    assert not safe_api_url("https://user:pass@hub.example")
