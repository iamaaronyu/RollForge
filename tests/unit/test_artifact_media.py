from types import SimpleNamespace

import pytest
from rollforge_api.artifacts import (
    read_artifact_download,
    read_artifact_media,
    read_external_trajectory,
)
from rollforge_api.routes import ApiFailure
from rollforge_schemas.api import ErrorCode


class FakeObjectStore:
    def __init__(self, *_args, **_kwargs):
        self.reads = []
        self.closed = False
        self.payload = b"verified media"

    def read_verified(self, key, digest, size):
        self.reads.append((key, digest, size))
        return self.payload

    def close(self):
        self.closed = True


def install_fake_store(monkeypatch, files):
    store = FakeObjectStore()
    monkeypatch.setattr("rollforge_api.artifacts.S3ObjectStore", lambda *_a, **_kw: store)
    monkeypatch.setattr(
        "rollforge_api.artifacts.BundlePublisher",
        lambda *_a, **_kw: SimpleNamespace(
            inspect=lambda scope, digest: SimpleNamespace(files=files)
        ),
    )
    return store


def fake_settings():
    return SimpleNamespace(
        object_storage_endpoint="https://objects.invalid",
        object_storage_bucket="bucket",
        object_storage_access_key="access",
        object_storage_secret_key="secret",
        object_storage_allow_local_http=False,
    )


def test_media_read_is_manifest_scoped_and_digest_checked(monkeypatch):
    record = SimpleNamespace(path="nested/photo.PNG", digest="sha256:expected", size=14)
    store = install_fake_store(monkeypatch, [record])

    data, media_type = read_artifact_media(
        fake_settings(),
        SimpleNamespace(prefix="jobs/job/execution"),
        SimpleNamespace(manifest_digest="sha256:manifest"),
        record.path,
    )

    assert data == b"verified media"
    assert media_type == "image/png"
    assert store.reads == [("jobs/job/execution/files/nested/photo.PNG", "sha256:expected", 14)]
    assert store.closed


@pytest.mark.parametrize(
    ("path", "size", "status", "message"),
    [
        ("missing.png", 4, 404, "产物不存在"),
        ("page.html", 4, 415, "不是支持预览的图片或音频"),
        ("too-large.mp3", 8 * 1024 * 1024 + 1, 413, "最多支持 8 MiB"),
    ],
)
def test_media_read_enforces_manifest_type_and_size(monkeypatch, path, size, status, message):
    files = (
        [] if path == "missing.png" else [SimpleNamespace(path=path, digest="sha256:x", size=size)]
    )
    store = install_fake_store(monkeypatch, files)

    with pytest.raises(ApiFailure, match=message) as error:
        read_artifact_media(
            fake_settings(),
            SimpleNamespace(prefix="scope"),
            SimpleNamespace(manifest_digest="sha256:manifest"),
            path,
        )

    assert error.value.status == status
    assert error.value.error.code in {
        ErrorCode.NOT_FOUND,
        ErrorCode.INVALID_REQUEST,
    }
    assert store.reads == []
    assert store.closed


def test_artifact_download_uses_manifest_digest_and_safe_filename(monkeypatch):
    record = SimpleNamespace(path="nested/实验结果.zip", digest="sha256:expected", size=14)
    store = install_fake_store(monkeypatch, [record])

    data, disposition = read_artifact_download(
        fake_settings(),
        SimpleNamespace(prefix="jobs/job/execution"),
        SimpleNamespace(manifest_digest="sha256:manifest"),
        record.path,
    )

    assert data == b"verified media"
    assert disposition == "attachment; filename*=UTF-8''%E5%AE%9E%E9%AA%8C%E7%BB%93%E6%9E%9C.zip"
    assert store.reads == [("jobs/job/execution/files/nested/实验结果.zip", "sha256:expected", 14)]
    assert store.closed


def test_artifact_download_rejects_files_larger_than_object_limit(monkeypatch):
    from rollforge_object_store.s3 import MAX_OBJECT_BYTES

    record = SimpleNamespace(path="huge.bin", digest="sha256:expected", size=MAX_OBJECT_BYTES + 1)
    store = install_fake_store(monkeypatch, [record])

    with pytest.raises(ApiFailure, match="64 MiB") as error:
        read_artifact_download(
            fake_settings(),
            SimpleNamespace(prefix="scope"),
            SimpleNamespace(manifest_digest="sha256:manifest"),
            record.path,
        )

    assert error.value.status == 413
    assert store.reads == []
    assert store.closed


def test_external_trajectory_reads_only_manifest_listed_json(monkeypatch):
    import json

    payload = json.dumps(
        {
            "schema_version": "ATIF-v1.8",
            "steps": [{"step_id": 1, "source": "agent", "message": "synthetic"}],
        }
    ).encode()
    record = SimpleNamespace(
        path="subagents/worker.json", digest="sha256:expected", size=len(payload)
    )
    store = install_fake_store(monkeypatch, [record])
    store.payload = payload

    view = read_external_trajectory(
        fake_settings(),
        SimpleNamespace(prefix="jobs/job/execution"),
        SimpleNamespace(manifest_digest="sha256:manifest"),
        record.path,
    )

    assert view.steps[0].message == "synthetic"
    assert store.reads == [
        ("jobs/job/execution/files/subagents/worker.json", "sha256:expected", len(payload))
    ]
    assert store.closed


@pytest.mark.parametrize(
    ("path", "files", "status", "opened_store"),
    [
        ("missing.json", [], 404, True),
        ("agent/trajectory.json", [], 404, False),
        ("notes.txt", [SimpleNamespace(path="notes.txt", digest="sha256:x", size=1)], 415, False),
        (
            "huge.json",
            [SimpleNamespace(path="huge.json", digest="sha256:x", size=1024 * 1024 + 1)],
            413,
            True,
        ),
    ],
)
def test_external_trajectory_rejects_unlisted_non_json_or_large_files(
    monkeypatch, path, files, status, opened_store
):
    store = install_fake_store(monkeypatch, files)
    with pytest.raises(ApiFailure) as error:
        read_external_trajectory(
            fake_settings(),
            SimpleNamespace(prefix="scope"),
            SimpleNamespace(manifest_digest="sha256:manifest"),
            path,
        )
    assert error.value.status == status
    assert store.reads == []
    assert store.closed is opened_store
