"""离线 Worker 边界检查；不代表真实 Harbor/E2B/模型验收。"""

import asyncio
import io
import tarfile
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from rollforge_schemas.domain import RevisionRef
from rollforge_schemas.execution import ExecutionSnapshot
from rollforge_schemas.runnable import RunnableBinding, digest_bytes, safe_key
from rollforge_worker.executor import Worker, WorkerError, WorkerSettings, runtime_environment
from rollforge_worker.tasks import atomic_write, pack_task, protected_file, unpack_task


def test_task_round_trip_preserves_identity_and_executable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "instruction.md").write_text("完成任务")
    (source / "solve.sh").write_text("#!/bin/sh\nexit 0\n")
    (source / "solve.sh").chmod(0o755)
    archive, identity = pack_task(source)
    destination = tmp_path / "extracted"
    unpack_task(archive, destination, digest_bytes(archive), identity)
    assert pack_task(destination)[1] == identity
    assert (destination / "solve.sh").stat().st_mode & 0o111
    with pytest.raises(ValueError):
        unpack_task(archive, destination, digest_bytes(archive), identity)
    with pytest.raises(ValueError):
        unpack_task(archive, tmp_path / "wrong", "sha256:" + "0" * 64, identity)


@pytest.mark.parametrize("name", ["../escape", "/escape", ".", "a/../escape", "a\\b", "a\n"])
def test_archive_paths_rejected_before_extraction(tmp_path, name):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        archive.addfile(entry, io.BytesIO())
    data = output.getvalue()
    destination = tmp_path / "task"
    with pytest.raises(ValueError):
        unpack_task(data, destination, digest_bytes(data), "sha256:" + "0" * 64)
    assert not destination.exists()
    with pytest.raises(ValueError):
        safe_key(name)


def test_archive_links_rejected(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        entry = tarfile.TarInfo("link")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "../escape"
        archive.addfile(entry)
    data = output.getvalue()
    with pytest.raises(ValueError):
        unpack_task(data, tmp_path / "task", digest_bytes(data), "sha256:" + "0" * 64)
    assert not (tmp_path / "task").exists()


def session_file(tmp_path: Path):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    path = parent / "runtime.env"
    path.write_text(
        "ROLLFORGE_MODEL_SESSION_TOKEN=limited-session\nE2B_API_KEY=test-e2b\n"
        "ANTHROPIC_API_KEY=upstream-secret\nROLLFORGE_WORKER_HUB_TOKEN=hub-secret\n"
        "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY=s3-secret\n"
    )
    path.chmod(0o600)
    return path


def test_runtime_credentials_are_allowlisted_without_environment_expansion(tmp_path, monkeypatch):
    path = session_file(tmp_path)
    monkeypatch.setenv("ROLLFORGE_OBJECT_STORAGE_SECRET_KEY", "platform-secret")
    settings = WorkerSettings(worker_runtime_env_file=path)
    env = runtime_environment(settings, tmp_path)
    assert env["ANTHROPIC_API_KEY"] == "limited-session"
    assert env["E2B_API_KEY"] == "test-e2b"
    assert "platform-secret" not in env.values()
    assert "upstream-secret" not in env.values()
    assert not any("HUB" in name or "STORAGE" in name for name in env)
    path.write_text(
        "ROLLFORGE_MODEL_SESSION_TOKEN=${ROLLFORGE_OBJECT_STORAGE_SECRET_KEY}\nE2B_API_KEY=test\n"
    )
    assert runtime_environment(settings, tmp_path)["ANTHROPIC_API_KEY"] == (
        "${ROLLFORGE_OBJECT_STORAGE_SECRET_KEY}"
    )
    path.chmod(0o644)
    with pytest.raises(ValueError):
        runtime_environment(settings, tmp_path)


def test_private_spool_files_reject_links_and_allow_atomic_replacement(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "lease.json"
    atomic_write(path, "first")
    atomic_write(path, "second")
    protected_file(path)
    assert path.read_text() == "second"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        protected_file(link)


def test_runnable_revision_binding_is_enforced():
    binding = RunnableBinding(
        task_archive_key="tasks/task.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.test",
    )
    snapshot = dict(
        task=RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64),
        agent=RevisionRef(id=uuid4(), revision=1, digest=binding.agent_digest),
        model=RevisionRef(id=uuid4(), revision=1, digest=binding.model_digest),
        runtime=binding,
    )
    assert ExecutionSnapshot(**snapshot).runtime == binding
    snapshot["agent"] = RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64)
    with pytest.raises(ValidationError):
        ExecutionSnapshot(**snapshot)


async def test_disabled_worker_does_not_claim_or_resume(tmp_path):
    worker = Worker(WorkerSettings(worker_runs_enabled=False), None, None)
    with pytest.raises(WorkerError):
        await worker.once()
    with pytest.raises(WorkerError):
        await worker.resume(tmp_path)


async def test_lease_loss_waits_for_operation_cleanup():
    class LosingWorker(Worker):
        async def heartbeat(self, _lease):
            await asyncio.sleep(0)
            raise WorkerError("lease lost")

    cleaned = asyncio.Event()

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            cleaned.set()

    worker = LosingWorker(WorkerSettings(), None, None)
    with pytest.raises(WorkerError):
        await worker.protected(None, operation())
    assert cleaned.is_set()


def runnable_lease():
    from datetime import datetime, timedelta

    from rollforge_schemas.execution import Lease

    binding = RunnableBinding(
        task_archive_key="tasks/task.tar.gz",
        task_archive_digest="sha256:" + "a" * 64,
        model_base_url="https://gateway.test",
    )
    return Lease(
        job_id=uuid4(),
        trial_id=uuid4(),
        execution_id=uuid4(),
        worker_id=uuid4(),
        fencing_token=1,
        expires_at=datetime.now(UTC) + timedelta(seconds=300),
        snapshot=ExecutionSnapshot(
            task=RevisionRef(id=uuid4(), revision=1, digest="sha256:" + "a" * 64),
            agent=RevisionRef(id=uuid4(), revision=1, digest=binding.agent_digest),
            model=RevisionRef(id=uuid4(), revision=1, digest=binding.model_digest),
            runtime=binding,
        ),
    )


def test_worker_rejects_unapproved_gateway_and_requires_explicit_http():
    lease = runnable_lease()
    worker = Worker(WorkerSettings(worker_gateway_url="https://other.test"), None, None)
    with pytest.raises(WorkerError):
        worker.check_binding(lease)
    binding = lease.snapshot.runtime.model_copy(update={"model_base_url": "http://gateway.test"})
    lease = lease.model_copy(
        update={"snapshot": lease.snapshot.model_copy(update={"runtime": binding})}
    )
    worker.settings = WorkerSettings(worker_gateway_url="http://gateway.test")
    with pytest.raises(WorkerError):
        worker.check_binding(lease)
    worker.settings = WorkerSettings(
        worker_gateway_url="http://gateway.test", worker_allow_lan_http=True
    )
    assert worker.check_binding(lease) == binding


async def test_pending_submission_replay_never_reruns_runtime(tmp_path):
    from rollforge_schemas.api import FinishRequest
    from rollforge_schemas.execution import ResultCommit, TrialView
    from rollforge_worker.executor import reference

    lease = runnable_lease()
    tmp_path.chmod(0o700)
    body = FinishRequest(
        lease=reference(lease),
        result=ResultCommit(
            outcome="SCORED",
            rewards={"reward": 0},
            manifest_key=(
                f"jobs/{lease.job_id}/trials/{lease.trial_id}/"
                f"executions/{lease.execution_id}/manifest.json"
            ),
            manifest_digest="sha256:" + "a" * 64,
        ),
    )
    atomic_write(tmp_path / "lease.json", lease.model_dump_json())
    atomic_write(tmp_path / "pending-commit.json", body.model_dump_json())

    class LostResponseHub:
        calls = 0

        async def finish(self, submitted):
            assert submitted == body
            self.calls += 1
            if self.calls == 1:
                raise WorkerError("response lost after acceptance")
            return TrialView(
                job_id=lease.job_id,
                trial_id=lease.trial_id,
                status="COMPLETED",
                fencing_token=1,
                result=body.result,
            )

    hub = LostResponseHub()
    worker = Worker(
        WorkerSettings(worker_runs_enabled=True, worker_gateway_url="https://gateway.test"),
        hub,
        None,
    )
    with pytest.raises(WorkerError):
        await worker.resume(tmp_path)
    assert not (tmp_path / "committed.json").exists()
    assert (await worker.resume(tmp_path))["status"] == "COMPLETED"
    assert hub.calls == 2 and (tmp_path / "committed.json").is_file()
    changed = body.model_copy(update={"lease": body.lease.model_copy(update={"fencing_token": 2})})
    atomic_write(tmp_path / "pending-commit.json", changed.model_dump_json())
    with pytest.raises(WorkerError):
        await worker.resume(tmp_path)
    assert hub.calls == 2
