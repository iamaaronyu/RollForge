import json
from uuid import uuid4

import pytest
from pydantic import SecretStr
from rollforge_object_store.bundle import BundlePublisher, canonical
from rollforge_object_store.s3 import ObjectConflict, ObjectStoreError, digest
from rollforge_schemas.runtime import ResultOutcome
from rollforge_schemas.storage import ArtifactManifest, ExecutionScope, StoredFile


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.fail_once = False

    def put_immutable(self, key, data):
        if key in self.objects and self.objects[key] != data:
            raise ObjectConflict("不可变对象冲突")
        self.objects[key] = data
        if self.fail_once:
            self.fail_once = False
            raise ObjectStoreError("注入写入后响应丢失")

    def read(self, key, maximum):
        if key not in self.objects:
            raise ObjectStoreError("缺少对象")
        data = self.objects[key]
        if len(data) > maximum:
            raise ObjectStoreError("对象超限")
        return data

    def read_verified(self, key, expected, size):
        data = self.read(key, size)
        if len(data) != size or digest(data) != expected:
            raise ObjectConflict("摘要不一致")
        return data


@pytest.fixture
def scope():
    return ExecutionScope(job_id=uuid4(), trial_id=uuid4(), execution_id=uuid4(), fencing_token=1)


@pytest.fixture
def output(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    (root / "result.json").write_text(
        json.dumps(
            {
                "started_at": "2026-10-06T00:00:00Z",
                "finished_at": "2026-10-06T00:00:01Z",
                "verifier_result": {"rewards": {"reward": 0.0}},
                "exception_info": None,
            }
        )
    )
    (root / "agent").mkdir()
    (root / "agent/trajectory.json").write_text('{"steps":[]}')
    return root


def test_lost_upload_response_is_recoverable_without_replacing_objects(scope, output):
    store = MemoryStore()
    store.fail_once = True
    with pytest.raises(ObjectStoreError):
        BundlePublisher(store, sensitive_values=()).publish(scope, output)
    assert scope.manifest_key not in store.objects
    before = dict(store.objects)
    publisher = BundlePublisher(store, sensitive_values=())
    result = publisher.publish(scope, output)
    assert result.outcome == ResultOutcome.SCORED and result.rewards == {"reward": 0.0}
    assert all(store.objects[key] == data for key, data in before.items())
    assert publisher.publish(scope, output) == result
    assert publisher.verify(scope, result.manifest_digest) == result


@pytest.mark.parametrize("in_filename", [False, True])
def test_secrets_are_rejected_before_any_upload(scope, output, in_filename):
    token = "synthetic-private-session-only"
    (output / (token if in_filename else "late-secret.txt")).write_text(
        "safe" if in_filename else token
    )
    store = MemoryStore()
    with pytest.raises(ValueError, match="凭证命中"):
        BundlePublisher(store, sensitive_values=(SecretStr(token),)).publish(scope, output)
    assert store.objects == {}


@pytest.mark.parametrize("directory", [False, True])
def test_symlinks_are_rejected_before_upload(scope, output, tmp_path, directory):
    target = tmp_path / "private"
    if directory:
        target.mkdir()
        (target / "secret").write_text("private")
    else:
        target.write_text("private")
    (output / "reference").symlink_to(target, target_is_directory=directory)
    store = MemoryStore()
    with pytest.raises(ValueError, match="符号链接"):
        BundlePublisher(store, sensitive_values=()).publish(scope, output)
    assert store.objects == {}


def test_changed_output_cannot_overwrite_existing_execution(scope, output):
    store = MemoryStore()
    publisher = BundlePublisher(store, sensitive_values=())
    first = publisher.publish(scope, output)
    (output / "agent/trajectory.json").write_text("different")
    with pytest.raises(ObjectConflict):
        publisher.publish(scope, output)
    assert publisher.verify(scope, first.manifest_digest) == first


def test_nested_and_dotted_paths_have_canonical_order(scope, output):
    (output / "agent.txt").write_text("root file")
    manifest = BundlePublisher(MemoryStore(), sensitive_values=()).prepare(scope, output)
    names = [file.path for file in manifest.files]
    assert names == sorted(names)


def test_change_during_upload_never_publishes_manifest(scope, output):
    class MutatingStore(MemoryStore):
        def put_immutable(self, key, data):
            super().put_immutable(key, data)
            (output / "result.json").write_text("changed after preflight")

    store = MutatingStore()
    with pytest.raises(ObjectConflict, match="上传期间改变"):
        BundlePublisher(store, sensitive_values=()).publish(scope, output)
    assert scope.manifest_key not in store.objects


def test_forged_manifest_summary_is_checked_against_native_result(scope, output):
    store = MemoryStore()
    publisher = BundlePublisher(store, sensitive_values=())
    publisher.publish(scope, output)
    manifest = ArtifactManifest.model_validate_json(store.objects[scope.manifest_key])
    forged = manifest.model_copy(
        update={
            "summary": manifest.summary.model_copy(update={"rewards": {"reward": 999.0}}),
        }
    )
    payload = canonical(forged)
    store.objects[scope.manifest_key] = payload
    with pytest.raises(ObjectConflict, match="原始 Runtime"):
        publisher.verify(scope, digest(payload))


@pytest.mark.parametrize("name", ["../private", "/absolute", "a//b", "a/../b", "a\x00b", "."])
def test_manifest_path_traversal_is_rejected(name):
    with pytest.raises(ValueError):
        StoredFile(path=name, size=0, digest="sha256:" + "0" * 64)
