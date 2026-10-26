import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.check_deployment import (
    PreflightError,
    validate_compose_config,
    validate_environment,
)


def write_env(path: Path, values: dict[str, str], mode: int = 0o600):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(mode)
    return path


def credential(role: str, token: str):
    return {
        "subject_id": str(uuid4()),
        "role": role,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
    }


def deployment_files(tmp_path: Path):
    repo = tmp_path / "repo"
    units = repo / "infra/systemd"
    units.mkdir(parents=True)
    sandbox = (
        "UMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n"
        "ProtectSystem=strict\nProtectHome=true\n"
    )
    (units / "rollforge-hub.service").write_text(
        "User=rollforge\nGroup=rollforge\n"
        "EnvironmentFile=/etc/rollforge/hub.env\n"
        "ExecStart=/opt/rollforge/.venv/bin/uvicorn rollforge_api.main:app "
        "--host 127.0.0.1 --port 8000\n"
        f"{sandbox}"
    )
    (units / "rollforge-web.service").write_text(
        "User=rollforge\nGroup=rollforge\n"
        "EnvironmentFile=/etc/rollforge/web.env\n"
        "ExecStart=/usr/bin/node /opt/rollforge/apps/hub-web/node_modules/next/dist/bin/next "
        "start --hostname 127.0.0.1 --port ${ROLLFORGE_WEB_HOST_PORT}\n"
        "ReadWritePaths=/opt/rollforge/apps/hub-web/.next/cache\n"
        f"{sandbox}"
    )

    user_token = "private-user-token-with-more-than-32-characters"
    worker_token = "private-worker-token-with-more-than-32-characters"
    api_credentials = json.dumps(
        [credential("USER", user_token), credential("WORKER", worker_token)], separators=(",", ":")
    )
    compose_env = write_env(
        tmp_path / "infra.env",
        {
            "POSTGRES_USER": "rollforge",
            "POSTGRES_PASSWORD": "postgres-random-secret-at-least-24-chars",
            "POSTGRES_DB": "rollforge",
            "MINIO_ROOT_USER": "rollforge-storage",
            "MINIO_ROOT_PASSWORD": "minio-random-secret-at-least-24-chars",
            "ROLLFORGE_POSTGRES_HOST_PORT": "55432",
            "ROLLFORGE_REDIS_HOST_PORT": "56379",
            "ROLLFORGE_MINIO_HOST_PORT": "59000",
            "ROLLFORGE_MINIO_CONSOLE_HOST_PORT": "59001",
        },
    )
    hub_env = write_env(
        tmp_path / "hub.env",
        {
            "ROLLFORGE_ENVIRONMENT": "production",
            "ROLLFORGE_DATABASE_URL": "postgresql+asyncpg://rollforge:secret@127.0.0.1:55432/rollforge",
            "ROLLFORGE_API_CREDENTIALS": api_credentials,
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT": "http://127.0.0.1:59000",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET": "rollforge",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY": "hub-read-key",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY": "hub-secret-key",
            "ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP": "true",
            "ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED": "false",
        },
        0o640,
    )
    worker_env = write_env(
        tmp_path / "worker.env",
        {
            "ROLLFORGE_WORKER_HUB_URL": "http://127.0.0.1:8000",
            "ROLLFORGE_WORKER_HUB_TOKEN": worker_token,
            "ROLLFORGE_WORKER_RUNS_ENABLED": "false",
            "ROLLFORGE_SCHEDULER_ENABLED": "false",
            "ROLLFORGE_SCHEDULER_CONCURRENCY": "1",
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT": "http://127.0.0.1:59000",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET": "rollforge",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY": "worker-write-key",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY": "worker-secret-key",
            "ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP": "true",
        },
        0o640,
    )
    web_env = write_env(
        tmp_path / "web.env",
        {
            "ROLLFORGE_API_URL": "http://127.0.0.1:8000",
            "ROLLFORGE_WEB_USER_TOKEN": user_token,
            "ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS": "false",
            "ROLLFORGE_WEB_HOST_PORT": "3300",
        },
        0o640,
    )
    return repo, compose_env, hub_env, worker_env, web_env


def compose_config():
    return {
        "services": {
            "postgres": {
                "ports": [{"host_ip": "127.0.0.1", "published": "55432"}],
                "volumes": [
                    {"type": "volume", "source": "pg-data", "target": "/var/lib/postgresql/data"}
                ],
            },
            "redis": {"ports": [{"host_ip": "127.0.0.1", "published": "56379"}]},
            "minio": {
                "ports": [
                    {"host_ip": "127.0.0.1", "published": "59000"},
                    {"host_ip": "127.0.0.1", "published": "59001"},
                ],
                "volumes": [{"type": "volume", "source": "minio-data", "target": "/data"}],
            },
        },
        "volumes": {"pg-data": {}, "minio-data": {}},
    }


def test_preflight_accepts_isolated_ports_and_closed_gates(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    validate_environment(
        repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
    )
    validate_compose_config(compose_config(), port_probe=lambda *_: True)


def test_preflight_rejects_port_conflicts_and_public_bindings():
    config = compose_config()
    with pytest.raises(PreflightError, match="已被占用"):
        validate_compose_config(config, port_probe=lambda _host, port: port != 56379)
    config = compose_config()
    config["services"]["minio"]["ports"][0]["host_ip"] = "0.0.0.0"
    with pytest.raises(PreflightError, match="127.0.0.1"):
        validate_compose_config(config, port_probe=lambda *_: True)


def test_preflight_rejects_configured_web_port_conflict():
    with pytest.raises(PreflightError, match="与 RollForge 服务冲突"):
        validate_compose_config(compose_config(), port_probe=lambda *_: True, web_port=59001)


def test_preflight_rejects_open_write_or_execution_gates(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    values = dict(line.split("=", 1) for line in worker_env.read_text().splitlines())
    values["ROLLFORGE_SCHEDULER_ENABLED"] = "true"
    write_env(worker_env, values, 0o640)
    with pytest.raises(PreflightError, match="Scheduler 关闭"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )


def test_preflight_rejects_web_token_not_owned_by_hub_user(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    write_env(
        web_env,
        {
            "ROLLFORGE_API_URL": "http://127.0.0.1:8000",
            "ROLLFORGE_WEB_USER_TOKEN": "wrong-user-token-with-more-than-32-characters",
            "ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS": "false",
            "ROLLFORGE_WEB_HOST_PORT": "3300",
        },
        0o640,
    )
    with pytest.raises(PreflightError, match="USER 凭证不匹配"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )


def test_preflight_refuses_insecure_compose_file_permissions(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    compose_env.chmod(0o644)
    with pytest.raises(PreflightError, match="必须为 0600"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )


def test_preflight_rejects_unreplaced_example_secrets(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    compose_values = dict(line.split("=", 1) for line in compose_env.read_text().splitlines())
    compose_values["POSTGRES_PASSWORD"] = "replace-with-a-unique-random-secret-at-least-24-chars"
    write_env(compose_env, compose_values)
    with pytest.raises(PreflightError, match="独立随机强凭证"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )


def test_preflight_rejects_direct_upstream_key_in_service_env(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    hub_values = dict(line.split("=", 1) for line in hub_env.read_text().splitlines())
    hub_values["ANTHROPIC_API_KEY"] = "must-not-be-forwarded"
    write_env(hub_env, hub_values, 0o640)
    with pytest.raises(PreflightError, match="ANTHROPIC_API_KEY"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )


def test_preflight_rejects_non_loopback_http_object_store(tmp_path):
    files = deployment_files(tmp_path)
    repo, compose_env, hub_env, worker_env, web_env = files
    hub_values = dict(line.split("=", 1) for line in hub_env.read_text().splitlines())
    hub_values["ROLLFORGE_OBJECT_STORAGE_ENDPOINT"] = "http://192.168.50.20:59000"
    write_env(hub_env, hub_values, 0o640)
    with pytest.raises(PreflightError, match="必须使用 HTTPS"):
        validate_environment(
            repo, compose_env, hub_env, worker_env, web_env, None, node_check=lambda: None
        )
