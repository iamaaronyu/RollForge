import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.backup_rollforge import (
    BackupError,
    postgres_environment,
    record_file,
    require_no_active_gateway_sessions,
    require_separate_restore_targets,
    restore_budget_database,
    snapshot_sqlite,
    verify_bundle,
    verify_referenced_objects,
)


def budget_database(parent: Path, rows: dict[str, int]):
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    path = parent / "gateway.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE session_usage ("
            "token_sha256 TEXT PRIMARY KEY, "
            "used_requests INTEGER NOT NULL CHECK (used_requests >= 0))"
        )
        connection.executemany(
            "INSERT INTO session_usage(token_sha256, used_requests) VALUES (?, ?)",
            rows.items(),
        )
    path.chmod(0o600)
    return path


def read_budget(path: Path):
    with sqlite3.connect(path) as connection:
        return dict(connection.execute("SELECT token_sha256, used_requests FROM session_usage"))


def test_budget_restore_merges_counters_monotonically_and_keeps_newer_rows(tmp_path):
    first, second, later = "a" * 64, "b" * 64, "c" * 64
    live = budget_database(tmp_path / "live-private", {first: 5, later: 1})
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    backup = bundle / "gateway-budget.sqlite3"
    snapshot_sqlite(budget_database(tmp_path / "source-private", {first: 7, second: 2}), backup)

    restore_budget_database(backup, live, gateway_stopped=True, sessions_revoked=False)

    assert read_budget(live) == {first: 7, second: 2, later: 1}
    assert live.stat().st_mode & 0o777 == 0o600


def test_new_host_budget_restore_requires_all_sessions_revoked(tmp_path):
    token_hash = "d" * 64
    backup = budget_database(tmp_path / "bundle-private", {token_hash: 4})
    live_dir = tmp_path / "recovery-private"
    live_dir.mkdir(mode=0o700)
    live_dir.chmod(0o700)

    with pytest.raises(BackupError, match="撤销全部旧模型会话"):
        restore_budget_database(
            backup,
            live_dir / "gateway.sqlite3",
            gateway_stopped=True,
            sessions_revoked=False,
        )

    restore_budget_database(
        backup,
        live_dir / "gateway.sqlite3",
        gateway_stopped=True,
        sessions_revoked=True,
    )
    assert read_budget(live_dir / "gateway.sqlite3") == {token_hash: 4}


def test_new_host_config_must_have_no_active_sessions(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    config = private / "gateway.json"
    config.write_text(json.dumps({"upstream_key": "not-revealed", "sessions": {}}))
    config.chmod(0o600)
    require_no_active_gateway_sessions(config)

    config.write_text(json.dumps({"upstream_key": "not-revealed", "sessions": {"token-hash": {}}}))
    with pytest.raises(BackupError, match="不得签发替代会话"):
        require_no_active_gateway_sessions(config)


def test_budget_restore_requires_gateway_to_be_stopped(tmp_path):
    token_hash = "e" * 64
    backup = budget_database(tmp_path / "backup-private", {token_hash: 1})
    live = budget_database(tmp_path / "live-private", {token_hash: 3})
    with pytest.raises(BackupError, match="停止模型网关"):
        restore_budget_database(backup, live, gateway_stopped=False, sessions_revoked=False)


def test_bundle_verify_checks_all_component_and_object_digests(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    bundle.chmod(0o700)
    budget = budget_database(tmp_path / "private", {"f" * 64: 2})
    (bundle / "gateway-budget.sqlite3").write_bytes(budget.read_bytes())
    (bundle / "gateway-budget.sqlite3").chmod(0o600)
    (bundle / "database.dump").write_bytes(b"independent PG dump payload")
    (bundle / "database.dump").chmod(0o600)
    objects = bundle / "objects"
    objects.mkdir(mode=0o700)
    key = "executions/job/manifest.json"
    object_path = objects / hashlib.sha256(key.encode()).hexdigest()
    payload = b"immutable manifest bytes"
    object_path.write_bytes(payload)
    object_path.chmod(0o600)
    object_record = {
        "key": key,
        "path": object_path.relative_to(bundle).as_posix(),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "metadata": {},
    }
    index = objects / "index.jsonl"
    index.write_text(json.dumps(object_record) + "\n")
    index.chmod(0o600)
    files = [
        record_file(bundle, bundle / "database.dump"),
        record_file(bundle, bundle / "gateway-budget.sqlite3"),
        record_file(bundle, object_path),
        record_file(bundle, index),
    ]
    manifest = {
        "format_version": 1,
        "created_at": "2026-10-26T00:00:00+00:00",
        "object_count": 1,
        "database_fingerprint": {
            "state": {"jobs": 1, "trials_by_status": {"COMPLETED": 1}},
            "reference_count": 1,
            "reference_sha256": "0" * 64,
        },
        "files": files,
    }
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)

    assert verify_bundle(bundle)["object_count"] == 1
    object_path.write_bytes(b"tampered")
    with pytest.raises(BackupError, match="摘要或大小"):
        verify_bundle(bundle)


def test_database_references_require_objects_and_manifest_content_digests():
    key = "executions/job/manifest.json"
    digest = "a" * 64
    references = {"object_keys": {key}, "manifest_digests": {key: f"sha256:{digest}"}}
    objects = [{"key": key, "sha256": digest}]
    verify_referenced_objects(references, objects)

    with pytest.raises(BackupError, match="摘要不一致"):
        verify_referenced_objects(references, [{"key": key, "sha256": "b" * 64}])
    with pytest.raises(BackupError, match="对象缺失"):
        verify_referenced_objects(references, [])


def test_postgres_password_is_kept_out_of_process_environment(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    secret = "do-not-show:this\\password"
    environment = postgres_environment(
        "postgresql+asyncpg://rollforge:do-not-show%3Athis%5Cpassword@127.0.0.1:55432/rollforge",
        private,
    )
    passfile = Path(environment["PGPASSFILE"])
    assert secret not in " ".join(environment.values())
    assert passfile.stat().st_mode & 0o777 == 0o600
    assert "do-not-show\\:this" in passfile.read_text()


def test_restore_target_must_be_separate_from_source(monkeypatch):
    monkeypatch.setenv(
        "ROLLFORGE_DATABASE_URL", "postgresql+asyncpg://rollforge:secret@127.0.0.1:55432/source"
    )
    monkeypatch.setenv("ROLLFORGE_OBJECT_STORAGE_ENDPOINT", "https://objects.example")
    monkeypatch.setenv("ROLLFORGE_OBJECT_STORAGE_BUCKET", "production")
    monkeypatch.setenv("ROLLFORGE_RESTORE_OBJECT_STORAGE_ENDPOINT", "https://objects.example")
    monkeypatch.setenv("ROLLFORGE_RESTORE_OBJECT_STORAGE_BUCKET", "production")

    with pytest.raises(BackupError, match="与源数据库相同"):
        require_separate_restore_targets("postgresql://rollforge:another@127.0.0.1:55432/source")

    with pytest.raises(BackupError, match="与源 Bucket 相同"):
        require_separate_restore_targets("postgresql://rollforge:another@127.0.0.1:55432/restore")
