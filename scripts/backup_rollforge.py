"""Create and verify a private RollForge recovery bundle; restore only to isolated targets."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.engine import make_url


class BackupError(Exception):
    """Safe-to-display backup/restore failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def record_file(root: Path, path: Path) -> dict[str, str | int]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def require_private_directory(path: Path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise BackupError("备份目录必须是仅当前用户可访问的普通目录")


def require_private_file(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise BackupError("私密状态文件必须是仅当前用户可访问的普通文件")


def validate_budget_database(path: Path):
    require_private_directory(path.parent)
    require_private_file(path)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise BackupError("网关预算账本完整性检查失败")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(session_usage)")}
            if not {"token_sha256", "used_requests"}.issubset(columns):
                raise BackupError("网关预算账本结构不符合当前契约")
            used_cost = "used_cost_microusd" if "used_cost_microusd" in columns else "0"
            reserved_cost = "reserved_cost_microusd" if "reserved_cost_microusd" in columns else "0"
            rows = connection.execute(
                f"SELECT token_sha256, used_requests, {used_cost}, {reserved_cost} "
                "FROM session_usage"
            ).fetchall()
            for token_hash, used, used_microusd, reserved_microusd in rows:
                if (
                    not isinstance(token_hash, str)
                    or len(token_hash) != 64
                    or any(char not in "0123456789abcdef" for char in token_hash)
                    or not isinstance(used, int)
                    or used < 0
                    or not isinstance(used_microusd, int)
                    or used_microusd < 0
                    or not isinstance(reserved_microusd, int)
                    or reserved_microusd < 0
                ):
                    raise BackupError("网关预算账本包含无效记录")
    except sqlite3.Error:
        raise BackupError("无法读取网关预算账本") from None


def snapshot_sqlite(source: Path, destination: Path):
    validate_budget_database(source)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(destination) as destination_db:
                source_db.backup(destination_db)
                columns = {
                    row[1] for row in destination_db.execute("PRAGMA table_info(session_usage)")
                }
                for column in ("used_cost_microusd", "reserved_cost_microusd"):
                    if column not in columns:
                        destination_db.execute(
                            f"ALTER TABLE session_usage ADD COLUMN {column} "
                            "INTEGER NOT NULL DEFAULT 0 CHECK (" + column + " >= 0)"
                        )
        os.chmod(destination, 0o600)
        with destination.open("rb") as stream:
            os.fsync(stream.fileno())
        validate_budget_database(destination)
    except sqlite3.Error:
        raise BackupError("网关预算账本快照失败") from None


def sqlite_rows(path: Path) -> dict[str, tuple[int, int, int]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_usage)")}
        used_cost = "used_cost_microusd" if "used_cost_microusd" in columns else "0"
        reserved_cost = "reserved_cost_microusd" if "reserved_cost_microusd" in columns else "0"
        return {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                f"SELECT token_sha256, used_requests, {used_cost}, {reserved_cost} "
                "FROM session_usage"
            )
        }


def restore_budget_database(
    backup: Path,
    live: Path,
    *,
    gateway_stopped: bool,
    sessions_revoked: bool,
):
    if not gateway_stopped:
        raise BackupError("恢复预算账本前必须先停止模型网关")
    require_private_directory(live.parent)
    validate_budget_database(backup)
    if live.exists() or live.is_symlink():
        validate_budget_database(live)
        if any(Path(str(live) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise BackupError("预算账本存在 SQLite sidecar；先停止网关并检查其一致性")
    elif not sessions_revoked:
        raise BackupError("新主机恢复前必须撤销全部旧模型会话；不能自动签发新会话")

    descriptor, temporary_name = tempfile.mkstemp(prefix=".budget-restore-", dir=live.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source = live if live.exists() else backup
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(temporary) as merged_db:
                source_db.backup(merged_db)
                columns = {row[1] for row in merged_db.execute("PRAGMA table_info(session_usage)")}
                for column in ("used_cost_microusd", "reserved_cost_microusd"):
                    if column not in columns:
                        merged_db.execute(
                            f"ALTER TABLE session_usage ADD COLUMN {column} "
                            "INTEGER NOT NULL DEFAULT 0 CHECK (" + column + " >= 0)"
                        )
                if live.exists():
                    backup_rows = [
                        (token_hash, *values) for token_hash, values in sqlite_rows(backup).items()
                    ]
                    merged_db.execute("BEGIN IMMEDIATE")
                    merged_db.executemany(
                        "INSERT INTO session_usage(token_sha256, used_requests, "
                        "used_cost_microusd, "
                        "reserved_cost_microusd) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(token_sha256) DO UPDATE SET used_requests = "
                        "MAX(session_usage.used_requests, excluded.used_requests), "
                        "used_cost_microusd = MAX(session_usage.used_cost_microusd, "
                        "excluded.used_cost_microusd), reserved_cost_microusd = "
                        "MAX(session_usage.reserved_cost_microusd, "
                        "excluded.reserved_cost_microusd)",
                        backup_rows,
                    )
                    merged_db.commit()
                if merged_db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise BackupError("恢复后的预算账本完整性检查失败")
        os.chmod(temporary, 0o600)
        merged_rows = sqlite_rows(temporary)
        for token_hash, counters in sqlite_rows(backup).items():
            if any(
                merged_value < backup_value
                for merged_value, backup_value in zip(
                    merged_rows.get(token_hash, (-1, -1, -1)), counters, strict=True
                )
            ):
                raise BackupError("恢复后的模型请求预算小于备份值")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, live)
        directory_fd = os.open(live.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        validate_budget_database(live)
    except sqlite3.Error:
        raise BackupError("网关预算账本恢复失败") from None
    finally:
        temporary.unlink(missing_ok=True)


def require_no_active_gateway_sessions(config_path: Path):
    require_private_directory(config_path.parent)
    require_private_file(config_path)
    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        raise BackupError("无法安全读取模型网关配置") from None
    if not isinstance(config, dict):
        raise BackupError("模型网关配置结构无效")
    sessions = config.get("sessions", {})
    if not isinstance(sessions, dict) or sessions:
        raise BackupError("新主机恢复预算前必须撤销全部会话，且不得签发替代会话")


def postgres_environment(database_url: str, private_dir: Path) -> dict[str, str]:
    try:
        url = make_url(database_url)
    except (ValueError, TypeError):
        raise BackupError("PostgreSQL 连接配置无效") from None
    if not url.drivername.startswith("postgresql") or not url.database:
        raise BackupError("备份目标必须是 PostgreSQL 数据库")
    container = postgres_client_container()
    password_path = private_dir / "pgpass"
    host = url.host or "localhost"
    port = str(url.port or 5432)
    if any(char in value for value in (url.database, url.username or "", host) for char in "\r\n"):
        raise BackupError("PostgreSQL 连接配置包含非法换行")
    if container:
        env = {
            name: value
            for name, value in os.environ.items()
            if name
            in {
                "PATH",
                "HOME",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
                "DOCKER_CERT_PATH",
                "DOCKER_TLS_VERIFY",
                "XDG_RUNTIME_DIR",
            }
        }
        env.update({"PGHOST": "/var/run/postgresql", "PGPORT": "5432", "PGDATABASE": url.database})
    else:
        env = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
        env.update({"PGHOST": host, "PGPORT": port, "PGDATABASE": url.database})
    if url.username:
        env["PGUSER"] = url.username
    if container:
        return env
    for name in ("sslmode", "sslrootcert", "sslcert", "sslkey", "gssencmode"):
        if name in url.query:
            value = url.query[name]
            if isinstance(value, tuple):
                raise BackupError("PostgreSQL SSL 配置项重复")
            env["PG" + name.upper()] = value
    password = url.password
    if password is not None:
        if "\n" in password or "\r" in password:
            raise BackupError("PostgreSQL 密码包含非法换行")

        def escape_passfile(value: str) -> str:
            return value.replace("\\", "\\\\").replace(":", "\\:")

        fields = (host, port, url.database, url.username or "*", password)
        password_path.write_text(":".join(escape_passfile(value) for value in fields) + "\n")
        os.chmod(password_path, 0o600)
        env["PGPASSFILE"] = str(password_path)
    return env


def postgres_client_container() -> str:
    container = os.environ.get("ROLLFORGE_POSTGRES_CLIENT_CONTAINER", "")
    if container and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise BackupError("PostgreSQL 客户端容器名称无效")
    return container


def postgres_command(name: str, env: dict[str, str]) -> list[str]:
    if name not in {"pg_dump", "pg_restore", "psql"}:
        raise BackupError("PostgreSQL 客户端工具不受支持")
    container = postgres_client_container()
    if container:
        docker = shutil.which("docker")
        if not docker:
            raise BackupError("容器化 PostgreSQL 客户端需要 Docker CLI")
        command = [docker, "exec", "-i", "--user=postgres"]
        for variable in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER"):
            if variable in env:
                command.extend(("--env", f"{variable}={env[variable]}"))
        return [*command, container, name]
    binary = shutil.which(name)
    if not binary:
        raise BackupError(f"缺少 PostgreSQL 客户端工具：{name}")
    return [binary]


def run_postgres(name: str, arguments: list[str], env: dict[str, str]):
    try:
        result = subprocess.run(
            [*postgres_command(name, env), *arguments],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        raise BackupError(f"无法运行 PostgreSQL 工具：{name}") from None
    if result.returncode:
        raise BackupError(f"PostgreSQL {name} 失败；为保护连接信息已隐藏底层输出")
    return result.stdout.strip()


def write_postgres_dump(destination: Path, env: dict[str, str]):
    if not postgres_client_container():
        run_postgres(
            "pg_dump",
            [
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={destination}",
            ],
            env,
        )
        return

    command = [
        *postgres_command("pg_dump", env),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
    ]
    process = None
    try:
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            process = subprocess.Popen(
                command,
                env=env,
                stdout=stream,
                stderr=subprocess.DEVNULL,
            )
            if process.wait() != 0:
                raise BackupError("PostgreSQL pg_dump 失败；已隐藏底层输出")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, subprocess.SubprocessError):
        if process and process.poll() is None:
            process.kill()
            process.wait()
        destination.unlink(missing_ok=True)
        raise BackupError("无法安全运行 PostgreSQL pg_dump") from None
    except BackupError:
        destination.unlink(missing_ok=True)
        raise


def postgres_state_fingerprint(database_url: str, private_dir: Path) -> dict:
    env = postgres_environment(database_url, private_dir)
    state_sql = (
        "SELECT jsonb_build_object("
        "'jobs',(SELECT count(*) FROM jobs),"
        "'trials_by_status',(SELECT COALESCE("
        "jsonb_object_agg(status, n ORDER BY status), '{}'::jsonb) "
        "FROM (SELECT status, count(*) n FROM trials GROUP BY status) q),"
        "'executions_by_status',(SELECT COALESCE("
        "jsonb_object_agg(status, n ORDER BY status), '{}'::jsonb) "
        "FROM (SELECT status, count(*) n FROM executions GROUP BY status) q),"
        "'registry_assets_by_kind',(SELECT COALESCE("
        "jsonb_object_agg(kind, n ORDER BY kind), '{}'::jsonb) "
        "FROM (SELECT kind, count(*) n FROM registry_assets GROUP BY kind) q),"
        "'registry_revisions',(SELECT count(*) FROM registry_revisions)"
        ")::text"
    )
    try:
        state = json.loads(
            run_postgres(
                "psql",
                ["--no-psqlrc", "--tuples-only", "--no-align", f"--command={state_sql}"],
                env,
            )
        )
    except (ValueError, TypeError):
        raise BackupError("无法读取数据库状态摘要") from None

    rows_sql = (
        "SELECT table_name || E'\\t' || row_id || E'\\t' || row_json FROM ("
        "SELECT 'jobs' table_name, id::text row_id, to_jsonb(j)::text row_json FROM jobs j "
        "UNION ALL SELECT 'trials', id::text, to_jsonb(t)::text FROM trials t "
        "UNION ALL SELECT 'executions', id::text, to_jsonb(e)::text FROM executions e "
        "UNION ALL SELECT 'registry_assets', id::text, to_jsonb(a)::text FROM registry_assets a "
        "UNION ALL SELECT 'registry_revisions', asset_id::text || ':' || revision::text, "
        "to_jsonb(r)::text FROM registry_revisions r "
        "UNION ALL SELECT 'alembic_version', version_num, to_jsonb(v)::text FROM alembic_version v"
        ") AS row_data ORDER BY table_name, row_id"
    )
    rows_hash = hashlib.sha256()
    row_counts = {}
    row_count = 0
    process = None
    try:
        process = subprocess.Popen(
            [
                *postgres_command("psql", env),
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--command=" + rows_sql,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            fields = line.rstrip("\n").split("\t", 2)
            if len(fields) != 3:
                raise BackupError("数据库行摘要格式无效")
            rows_hash.update(line.encode())
            row_counts[fields[0]] = row_counts.get(fields[0], 0) + 1
            row_count += 1
        process.stdout.close()
        if process.wait() != 0:
            raise BackupError("无法读取数据库状态行摘要；已隐藏连接输出")
    except OSError:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        raise BackupError("无法运行 PostgreSQL 状态行检查") from None
    except BackupError:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        raise
    state["row_counts"] = row_counts
    state["row_count"] = row_count
    state["row_sha256"] = rows_hash.hexdigest()

    references_sql = (
        "SELECT source || E'\\t' || object_key || E'\\t' || object_digest FROM ("
        "SELECT 'registry_revision' source, asset_id::text || ':' || revision::text object_key, "
        "digest object_digest FROM registry_revisions "
        "UNION ALL SELECT 'task_revision', spec->>'archive_key', "
        "spec->>'archive_digest' FROM registry_revisions "
        "WHERE spec->>'kind'='TASK' "
        "UNION ALL SELECT 'job_task', snapshot #>> '{runtime,task_archive_key}', "
        "snapshot #>> '{runtime,task_archive_digest}' FROM jobs "
        "WHERE snapshot #>> '{runtime,task_archive_key}' IS NOT NULL "
        "UNION ALL SELECT 'execution_manifest', result->>'manifest_key', "
        "result->>'manifest_digest' FROM executions WHERE result IS NOT NULL "
        "AND result->>'manifest_key' IS NOT NULL"
        ") refs ORDER BY source, object_key, object_digest"
    )
    process = None
    reference_hash = hashlib.sha256()
    object_keys = set()
    manifest_digests = {}
    count = 0
    try:
        process = subprocess.Popen(
            [
                *postgres_command("psql", env),
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--command=" + references_sql,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 3 or not fields[1]:
                raise BackupError("数据库对象引用格式无效")
            source, key, object_digest = fields
            if source == "execution_manifest":
                if (
                    not object_digest
                    or not object_digest.startswith("sha256:")
                    or len(object_digest) != 71
                    or any(char not in "0123456789abcdef" for char in object_digest[7:])
                ):
                    raise BackupError("数据库中的 Manifest 摘要格式无效")
                manifest_digests[key] = object_digest
            if source in {"task_revision", "job_task", "execution_manifest"}:
                object_keys.add(key)
            reference_hash.update(line.encode())
            count += 1
        process.stdout.close()
        if process.wait() != 0:
            raise BackupError("无法读取数据库对象引用；已隐藏连接输出")
    except OSError:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        raise BackupError("无法运行 PostgreSQL 引用检查") from None
    except BackupError:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        raise
    return {
        "state": state,
        "reference_count": count,
        "reference_sha256": reference_hash.hexdigest(),
        "object_keys": object_keys,
        "manifest_digests": manifest_digests,
    }


def verify_referenced_objects(references: dict, objects: list[dict]):
    by_key = {item["key"]: item for item in objects}
    for key in references["object_keys"]:
        if key not in by_key:
            raise BackupError("数据库引用的 Revision、Task 或 Manifest 对象缺失")
    for key, expected_digest in references["manifest_digests"].items():
        if "sha256:" + by_key[key]["sha256"] != expected_digest:
            raise BackupError("对象存储 Manifest 内容与 PostgreSQL 摘要不一致")


def public_database_fingerprint(references: dict) -> dict:
    return {
        "state": references["state"],
        "reference_count": references["reference_count"],
        "reference_sha256": references["reference_sha256"],
    }


def s3_client_from_environment(prefix: str = "ROLLFORGE_OBJECT_STORAGE"):
    endpoint = os.environ.get(f"{prefix}_ENDPOINT", "")
    bucket = os.environ.get(f"{prefix}_BUCKET", "")
    access = os.environ.get(f"{prefix}_ACCESS_KEY", "")
    secret = os.environ.get(f"{prefix}_SECRET_KEY", "")
    try:
        url = urlsplit(endpoint)
        _port = url.port
    except ValueError:
        raise BackupError("对象存储地址无效") from None
    local_http = (
        url.scheme == "http"
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
        and os.environ.get(f"{prefix}_ALLOW_LOCAL_HTTP", "false").lower() == "true"
    )
    if (
        not bucket
        or not access
        or not secret
        or not url.hostname
        or (url.scheme != "https" and not local_http)
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise BackupError("对象存储必须配置凭证与 HTTPS；HTTP 仅允许显式 loopback")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, proxies={}),
    )
    return client, bucket


def object_file_path(root: Path, key: str) -> Path:
    return root / "objects" / hashlib.sha256(key.encode()).hexdigest()


def stream_object_to_file(client, bucket: str, key: str, path: Path):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        digest = hashlib.sha256()
        size = 0
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            body = response["Body"]
            try:
                while chunk := body.read(1024 * 1024):
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                body.close()
        if size != response["ContentLength"]:
            raise BackupError("对象存储读取长度与响应不一致")
        metadata = {
            name: response[name]
            for name in (
                "ContentType",
                "ContentEncoding",
                "CacheControl",
                "ContentDisposition",
                "ContentLanguage",
                "Metadata",
            )
            if response.get(name) is not None
        }
        if response.get("Expires"):
            metadata["Expires"] = response["Expires"].isoformat()
        return {
            "path": path.relative_to(path.parents[1]).as_posix(),
            "size": size,
            "sha256": digest.hexdigest(),
            "metadata": metadata,
        }
    except (BotoCoreError, ClientError, OSError):
        path.unlink(missing_ok=True)
        raise BackupError("对象存储快照读取失败") from None


def snapshot_objects(client, bucket: str, root: Path) -> tuple[list[dict], list[dict]]:
    records = []
    files = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for entry in page.get("Contents", []):
                key = entry["Key"]
                path = object_file_path(root, key)
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                record = stream_object_to_file(client, bucket, key, path)
                records.append({"key": key, **record})
                files.append(record_file(root, path))
    except (BotoCoreError, ClientError):
        raise BackupError("对象存储目录读取失败") from None
    records.sort(key=lambda item: item["key"])
    index = root / "objects" / "index.jsonl"
    with index.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(index, 0o600)
    files.append(record_file(root, index))
    return records, files


def verify_bundle(bundle: Path) -> dict:
    require_private_directory(bundle)
    manifest_path = bundle / "manifest.json"
    require_private_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("format_version") != 1:
            raise ValueError
        expected = {item["path"]: item for item in manifest["files"]}
        if len(expected) != len(manifest["files"]):
            raise ValueError
        for relative, record in expected.items():
            if (
                not isinstance(record["size"], int)
                or record["size"] < 0
                or len(record["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in record["sha256"])
            ):
                raise ValueError
            parts = relative.split("/")
            valid_path = relative in {
                "database.dump",
                "gateway-budget.sqlite3",
                "objects/index.jsonl",
            }
            valid_object = (
                len(parts) == 2
                and parts[0] == "objects"
                and len(parts[1]) == 64
                and all(char in "0123456789abcdef" for char in parts[1])
            )
            if not valid_path and not valid_object:
                raise ValueError
            candidate = bundle / relative
            if (
                any((bundle / part).is_symlink() for part in parts[:-1])
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                raise ValueError
            require_private_file(candidate)
            if (
                candidate.stat().st_size != record["size"]
                or sha256_file(candidate) != record["sha256"]
            ):
                raise BackupError("备份文件摘要或大小校验失败")
        index = bundle / "objects/index.jsonl"
        if "objects/index.jsonl" not in expected:
            raise ValueError
        seen_keys = set()
        for line in index.read_text().splitlines():
            item = json.loads(line)
            key, relative = item["key"], item["path"]
            expected_path = object_file_path(bundle, key).relative_to(bundle).as_posix()
            if key in seen_keys or relative != expected_path:
                raise ValueError
            seen_keys.add(key)
            record = expected.get(relative)
            if not record or record["size"] != item["size"] or record["sha256"] != item["sha256"]:
                raise ValueError
        indexed_paths = {item["path"] for item in object_index(bundle)}
        bundled_object_paths = {
            path
            for path in expected
            if path.startswith("objects/") and path != "objects/index.jsonl"
        }
        if indexed_paths != bundled_object_paths:
            raise ValueError
        if "database.dump" not in expected or "gateway-budget.sqlite3" not in expected:
            raise ValueError
        validate_budget_database(bundle / "gateway-budget.sqlite3")
        if len(seen_keys) != manifest["object_count"]:
            raise ValueError
        fingerprint = manifest["database_fingerprint"]
        if (
            not isinstance(fingerprint["state"], dict)
            or not isinstance(fingerprint["reference_count"], int)
            or fingerprint["reference_count"] < 0
            or len(fingerprint["reference_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint["reference_sha256"])
        ):
            raise ValueError
        expected_paths = set(expected)
        actual_paths = set()
        for path in bundle.rglob("*"):
            relative = path.relative_to(bundle).as_posix()
            if path.is_symlink():
                raise ValueError
            if path.is_dir():
                if relative != "objects":
                    raise ValueError
            elif path == manifest_path:
                continue
            elif path.is_file() and path != manifest_path:
                actual_paths.add(relative)
            else:
                raise ValueError
        if actual_paths != expected_paths:
            raise ValueError
        return manifest
    except BackupError:
        raise
    except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError):
        raise BackupError("备份清单或目录结构无效") from None


def create_backup(destination: Path, budget_db: Path, *, quiescent: bool):
    if not quiescent:
        raise BackupError("创建跨服务备份前必须确认 Hub 写入、Worker、Scheduler 与网关均已停止")
    database_url = os.environ.get("ROLLFORGE_DATABASE_URL", "")
    if not database_url:
        raise BackupError("需要通过受保护环境提供 ROLLFORGE_DATABASE_URL")
    require_private_directory(budget_db.parent)
    validate_budget_database(budget_db)
    parent = destination.parent
    require_private_directory(parent)
    if destination.exists() or destination.is_symlink():
        raise BackupError("备份目标必须是尚不存在的新目录")
    staging = parent / f".rollforge-backup-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="pg-credentials-", dir=staging) as private:
            initial_references = postgres_state_fingerprint(database_url, Path(private))
            database_fingerprint = public_database_fingerprint(initial_references)
            postgres_env = postgres_environment(database_url, Path(private))
            write_postgres_dump(staging / "database.dump", postgres_env)
        os.chmod(staging / "database.dump", 0o600)
        with (staging / "database.dump").open("rb") as stream:
            os.fsync(stream.fileno())
        snapshot_sqlite(budget_db, staging / "gateway-budget.sqlite3")
        # The Hub identity intentionally cannot enumerate the bucket. Backups
        # use a separate, read-only identity with ListBucket/GetObject access.
        client, bucket = s3_client_from_environment("ROLLFORGE_BACKUP_OBJECT_STORAGE")
        try:
            _objects, _object_files = snapshot_objects(client, bucket, staging)
        finally:
            client.close()
        with tempfile.TemporaryDirectory(prefix="pg-verify-", dir=staging) as private:
            final_references = postgres_state_fingerprint(database_url, Path(private))
        if public_database_fingerprint(final_references) != database_fingerprint:
            raise BackupError("备份期间 PostgreSQL 状态发生变化；请停止所有写入后重试")
        verify_referenced_objects(final_references, _objects)
        files = [
            record_file(staging, staging / "database.dump"),
            record_file(staging, staging / "gateway-budget.sqlite3"),
            *[record_file(staging, path) for path in sorted((staging / "objects").glob("*"))],
        ]
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "database_name": make_url(database_url).database,
            "database_fingerprint": database_fingerprint,
            "object_bucket": bucket,
            "object_count": len(_objects),
            "files": files,
            "consistency": (
                "writers-quiesced; PostgreSQL MVCC dump; immutable object snapshot; "
                "SQLite online backup"
            ),
            "restore_boundary": (
                "PostgreSQL rows/revisions and manifest digests are restored exactly; "
                "every bucket object is SHA-256 checked. Gateway session configuration is excluded."
            ),
        }
        manifest_path = staging / "manifest.json"
        payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        manifest_path.write_text(payload)
        os.chmod(manifest_path, 0o600)
        with manifest_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.rename(staging, destination)
        parent_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def database_is_empty(database_url: str, private_dir: Path) -> dict[str, str]:
    env = postgres_environment(database_url, private_dir)
    result = run_postgres(
        "psql",
        [
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--command=SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
            "AND nspname <> 'information_schema' AND nspname <> 'public') OR EXISTS (SELECT 1 "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') "
            "OR EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public') OR EXISTS (SELECT 1 FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') "
            "OR EXISTS (SELECT 1 FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace "
            "WHERE n.nspname='public')",
        ],
        env,
    )
    if result != "f":
        raise BackupError("PostgreSQL 恢复目标不是空数据库；拒绝覆盖")
    return env


def restore_database(bundle: Path, database_url: str, expected_fingerprint: dict):
    with tempfile.TemporaryDirectory(prefix="pg-restore-") as private:
        env = database_is_empty(database_url, Path(private))
        container_client = bool(postgres_client_container())
        dump_stream = (bundle / "database.dump").open("rb") if container_client else None
        try:
            restore_command = [
                *postgres_command("pg_restore", env),
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ]
            if not container_client:
                restore_command.append(str(bundle / "database.dump"))
            restore = subprocess.Popen(
                restore_command,
                stdin=dump_stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            psql = subprocess.run(
                [
                    *postgres_command("psql", env),
                    "--no-psqlrc",
                    "--set=ON_ERROR_STOP=1",
                    "--single-transaction",
                ],
                stdin=restore.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                check=False,
            )
            if restore.stdout:
                restore.stdout.close()
            restore.communicate()
        except OSError:
            if "restore" in locals() and restore.poll() is None:
                restore.kill()
                restore.wait()
            raise BackupError("PostgreSQL 恢复工具启动失败") from None
        finally:
            if dump_stream:
                dump_stream.close()
        if restore.returncode or psql.returncode:
            raise BackupError("PostgreSQL 恢复失败；为保护连接信息已隐藏底层输出")
        actual = public_database_fingerprint(
            postgres_state_fingerprint(database_url, Path(private))
        )
        if actual != expected_fingerprint:
            raise BackupError("恢复后的 Job/Trial/Execution/Revision 与 Manifest 引用摘要不一致")


def require_separate_restore_targets(database_url: str):
    source_url = os.environ.get("ROLLFORGE_DATABASE_URL")
    if source_url:
        try:
            source = make_url(source_url)
            target = make_url(database_url)
        except (ValueError, TypeError):
            raise BackupError("源或恢复 PostgreSQL 连接配置无效") from None
        source_identity = (
            source.drivername.split("+", 1)[0],
            source.username,
            source.host,
            source.port or 5432,
            source.database,
            source.query,
        )
        target_identity = (
            target.drivername.split("+", 1)[0],
            target.username,
            target.host,
            target.port or 5432,
            target.database,
            target.query,
        )
        if source.database == target.database or source_identity == target_identity:
            raise BackupError("恢复 PostgreSQL 目标与源数据库相同；拒绝原地覆盖")
    source_endpoint = os.environ.get("ROLLFORGE_OBJECT_STORAGE_ENDPOINT", "").rstrip("/")
    source_bucket = os.environ.get("ROLLFORGE_OBJECT_STORAGE_BUCKET", "")
    target_endpoint = os.environ.get("ROLLFORGE_RESTORE_OBJECT_STORAGE_ENDPOINT", "").rstrip("/")
    target_bucket = os.environ.get("ROLLFORGE_RESTORE_OBJECT_STORAGE_BUCKET", "")
    if (
        source_endpoint
        and source_bucket
        and (source_endpoint, source_bucket)
        == (
            target_endpoint,
            target_bucket,
        )
    ):
        raise BackupError("恢复对象存储目标与源 Bucket 相同；拒绝原地覆盖")


def object_index(bundle: Path) -> list[dict]:
    return [json.loads(line) for line in (bundle / "objects/index.jsonl").read_text().splitlines()]


def restore_objects(bundle: Path, client, bucket: str):
    try:
        expected = {item["key"]: item for item in object_index(bundle)}
        existing = {}
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for row in page.get("Contents", []):
                if row["Key"] not in expected:
                    raise BackupError("对象存储恢复目标含有备份之外的对象；拒绝合并")
                existing[row["Key"]] = row
        for key, item in expected.items():
            backup_path = bundle / item["path"]
            if key in existing:
                response = client.get_object(Bucket=bucket, Key=key)
                try:
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := response["Body"].read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                finally:
                    response["Body"].close()
                if digest.hexdigest() != item["sha256"] or size != item["size"]:
                    raise BackupError("对象存储已有对象与备份不一致")
                continue
            metadata = item.get("metadata", {})
            arguments = {
                name: metadata[name]
                for name in (
                    "ContentType",
                    "ContentEncoding",
                    "CacheControl",
                    "ContentDisposition",
                    "ContentLanguage",
                    "Metadata",
                )
                if name in metadata
            }
            if metadata.get("Expires"):
                arguments["Expires"] = datetime.fromisoformat(metadata["Expires"])
            with backup_path.open("rb") as body:
                client.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*", **arguments)
            response = client.get_object(Bucket=bucket, Key=key)
            try:
                digest = hashlib.sha256()
                size = 0
                while chunk := response["Body"].read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            finally:
                response["Body"].close()
            if digest.hexdigest() != item["sha256"] or size != item["size"]:
                raise BackupError("对象存储恢复后的摘要校验失败")
    except (BotoCoreError, ClientError, OSError):
        raise BackupError("对象存储恢复失败") from None


def restore_bundle(bundle: Path, database_url: str):
    manifest = verify_bundle(bundle)
    require_separate_restore_targets(database_url)
    with tempfile.TemporaryDirectory(prefix="pg-restore-preflight-") as private:
        database_is_empty(database_url, Path(private))
    client, bucket = s3_client_from_environment("ROLLFORGE_RESTORE_OBJECT_STORAGE")
    # 先恢复不可变对象，再恢复引用它们的任务状态；失败时留下的对象可安全重试。
    try:
        restore_objects(bundle, client, bucket)
    finally:
        client.close()
    restore_database(bundle, database_url, manifest["database_fingerprint"])
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="创建 PostgreSQL/S3/SQLite 一致性备份")
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--gateway-budget-db", type=Path, required=True)
    create.add_argument("--acknowledge-quiescent", action="store_true")
    verify = commands.add_parser("verify", help="只校验备份文件与摘要")
    verify.add_argument("--bundle", type=Path, required=True)
    restore = commands.add_parser("restore", help="恢复到空 PostgreSQL 与空/同内容 S3 bucket")
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--acknowledge-isolated-targets", action="store_true")
    budget = commands.add_parser("restore-budget", help="单调合并 SQLite 网关请求预算")
    budget.add_argument("--bundle", type=Path, required=True)
    budget.add_argument("--gateway-budget-db", type=Path, required=True)
    budget.add_argument("--acknowledge-gateway-stopped", action="store_true")
    budget.add_argument("--gateway-config", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            manifest = create_backup(
                args.destination,
                args.gateway_budget_db,
                quiescent=args.acknowledge_quiescent,
            )
            print(
                f"RollForge backup created: objects={manifest['object_count']} "
                f"files={len(manifest['files'])}"
            )
        elif args.command == "verify":
            manifest = verify_bundle(args.bundle)
            print(
                f"RollForge backup verified: objects={manifest['object_count']} "
                f"files={len(manifest['files'])}"
            )
        elif args.command == "restore":
            if not args.acknowledge_isolated_targets:
                raise BackupError("必须确认 PostgreSQL 与对象存储是隔离恢复目标")
            database_url = os.environ.get("ROLLFORGE_RESTORE_DATABASE_URL", "")
            if not database_url:
                raise BackupError("需要通过受保护环境提供 ROLLFORGE_RESTORE_DATABASE_URL")
            manifest = restore_bundle(args.bundle, database_url)
            print(f"RollForge data restored: objects={manifest['object_count']}; database=restored")
        else:
            manifest = verify_bundle(args.bundle)
            budget_backup = args.bundle / "gateway-budget.sqlite3"
            live_exists = args.gateway_budget_db.exists() or args.gateway_budget_db.is_symlink()
            sessions_revoked = False
            if not live_exists:
                if not args.gateway_config:
                    raise BackupError("新主机恢复必须提供已撤销全部会话的 --gateway-config")
                require_no_active_gateway_sessions(args.gateway_config)
                sessions_revoked = True
            restore_budget_database(
                budget_backup,
                args.gateway_budget_db,
                gateway_stopped=args.acknowledge_gateway_stopped,
                sessions_revoked=sessions_revoked,
            )
            print(f"RollForge gateway budget merged: bundle={manifest['created_at']}")
    except BackupError as exc:
        print(f"RollForge backup: FAILED ({exc})", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"RollForge backup: FAILED ({type(exc).__name__}; details suppressed)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
