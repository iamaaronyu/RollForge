"""Check a safe, loopback-only RollForge deployment before starting services."""

import argparse
import hashlib
import json
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


class PreflightError(Exception):
    pass


def reject_runtime_credentials(values: dict[str, str], path: Path):
    forbidden = {
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "E2B_API_KEY",
        "ROLLFORGE_MODEL_SESSION_TOKEN",
    }.intersection(values)
    if forbidden:
        raise PreflightError(
            f"{path} 含有不应置于该服务环境的凭证项：{', '.join(sorted(forbidden))}"
        )


def read_env(path: Path, *, private_mode: bool) -> dict[str, str]:
    try:
        info = path.lstat()
    except OSError:
        raise PreflightError(f"配置文件不可读：{path}") from None
    if not stat.S_ISREG(info.st_mode):
        raise PreflightError(f"配置路径必须是普通文件：{path}")
    mode = stat.S_IMODE(info.st_mode)
    if private_mode and mode != 0o600:
        raise PreflightError(f"配置文件必须为 0600：{path}")
    if not private_mode and mode & 0o137:
        raise PreflightError(f"配置文件权限过宽，最多允许 0640：{path}")
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise PreflightError(f"配置文件格式错误：{path}:{number}")
        if key in values:
            raise PreflightError(f"配置项重复：{path}:{key}")
        values[key] = value
    return values


def require(values: dict[str, str], names: tuple[str, ...], path: Path):
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise PreflightError(f"{path} 缺少必需配置项：{', '.join(missing)}")


def reject_unknown(values: dict[str, str], allowed: set[str], path: Path):
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise PreflightError(f"{path} 含有未识别配置项：{', '.join(unknown)}")


def validate_loopback_hub_url(value: str, path: Path, field: str):
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise PreflightError(f"{path} 的 {field} 无效") from None
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or port != 8000
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise PreflightError(f"{path} 的 {field} 必须指向本机 Hub 127.0.0.1:8000")


def validate_object_endpoint(values: dict[str, str], path: Path):
    endpoint = values["ROLLFORGE_OBJECT_STORAGE_ENDPOINT"]
    try:
        url = urlsplit(endpoint)
        _port = url.port
    except ValueError:
        raise PreflightError(f"{path} 的对象存储地址无效") from None
    local_http = (
        url.scheme == "http"
        and url.hostname in {"localhost", "127.0.0.1", "::1", "[::1]"}
        and values.get("ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP", "false").lower() == "true"
    )
    if (
        (url.scheme != "https" and not local_http)
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise PreflightError(f"{path} 的对象存储必须使用 HTTPS，或显式允许 loopback HTTP")


def credentials(config: str) -> list[dict[str, str]]:
    try:
        data = json.loads(config)
        if not isinstance(data, list) or not data:
            raise ValueError
        seen_hashes = set()
        roles = set()
        for item in data:
            if not isinstance(item, dict):
                raise ValueError
            UUID(item["subject_id"])
            role = item["role"]
            digest = item["token_sha256"]
            if role not in {"USER", "WORKER"} or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
            if digest in seen_hashes:
                raise ValueError
            seen_hashes.add(digest)
            roles.add(role)
        if not {"USER", "WORKER"}.issubset(roles):
            raise ValueError
        return data
    except (KeyError, TypeError, ValueError):
        raise PreflightError("ROLLFORGE_API_CREDENTIALS 格式无效") from None


def compose_config(repo: Path, env_file: Path) -> dict:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "--file",
        str(repo / "infra/docker-compose/compose.yaml"),
        "config",
        "--format",
        "json",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, cwd=repo)
    except OSError:
        raise PreflightError("需要已安装且可运行的 Docker Compose v2") from None
    if result.returncode:
        raise PreflightError("Docker Compose 配置校验失败；已隐藏配置解析输出")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise PreflightError("Docker Compose 未返回有效配置") from None


def check_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, port))
        except OSError:
            return False
    return True


def check_node_runtime():
    node = Path("/usr/bin/node")
    try:
        real_node = node.resolve(strict=True)
        result = subprocess.run(
            [str(node), "--version"], check=False, capture_output=True, text=True
        )
    except (OSError, RuntimeError):
        raise PreflightError("Web 服务需要安装系统级 Node.js 20.9 或更新版本") from None
    if any(real_node.is_relative_to(Path(home)) for home in ("/home", "/root")):
        raise PreflightError("Web 服务的 Node.js 不能依赖 ProtectHome 隔离的用户目录")
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    if result.returncode or not match:
        raise PreflightError("无法验证系统级 Node.js 版本")
    major, minor, _patch = (int(part) for part in match.groups())
    if major < 20 or (major == 20 and minor < 9):
        raise PreflightError("Web 服务需要 Node.js 20.9 或更新版本")


def validate_compose_config(config: dict, port_probe=check_port_available, web_port: int = 3300):
    services = config.get("services", {})
    volumes = config.get("volumes", {})
    bound_ports: set[int] = set()
    for service_name in ("postgres", "redis", "minio"):
        service = services.get(service_name)
        if not service:
            raise PreflightError(f"Compose 缺少服务：{service_name}")
        for port in service.get("ports", []):
            host_ip = port.get("host_ip")
            published = port.get("published")
            try:
                host_port = int(published)
            except (TypeError, ValueError):
                raise PreflightError(f"{service_name} 的主机端口无效") from None
            if host_ip != "127.0.0.1":
                raise PreflightError(f"{service_name} 必须只绑定 127.0.0.1")
            if not 1024 <= host_port <= 65535 or host_port in bound_ports:
                raise PreflightError(f"Compose 主机端口无效或重复：{host_port}")
            if not port_probe(host_ip, host_port):
                raise PreflightError(f"Compose 主机端口已被占用：{host_port}")
            bound_ports.add(host_port)

    service_ports = (8000, web_port)
    if len(set(service_ports)) != len(service_ports):
        raise PreflightError("Hub 与 Web 主机端口不能相同")
    for service_port in service_ports:
        if service_port in bound_ports:
            raise PreflightError(f"Compose 主机端口与 RollForge 服务冲突：{service_port}")
        if not port_probe("127.0.0.1", service_port):
            raise PreflightError(f"RollForge 服务主机端口已被占用：{service_port}")

    for service_name, target in (("postgres", "/var/lib/postgresql/data"), ("minio", "/data")):
        mounts = services[service_name].get("volumes", [])
        if not any(
            mount.get("type") == "volume"
            and mount.get("target") == target
            and mount.get("source") in volumes
            for mount in mounts
        ):
            raise PreflightError(f"{service_name} 缺少命名持久卷：{target}")


def validate_units(repo: Path):
    checks = {
        "rollforge-hub.service": (
            "User=rollforge",
            "Group=rollforge",
            "EnvironmentFile=/etc/rollforge/hub.env",
            "ExecStart=/opt/rollforge/.venv/bin/uvicorn rollforge_api.main:app "
            "--host 127.0.0.1 --port 8000",
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
        ),
        "rollforge-web.service": (
            "User=rollforge",
            "Group=rollforge",
            "EnvironmentFile=/etc/rollforge/web.env",
            "ExecStart=/usr/bin/node /opt/rollforge/apps/hub-web/node_modules/next/dist/bin/next "
            "start --hostname 127.0.0.1 --port ${ROLLFORGE_WEB_HOST_PORT}",
            "UMask=0077",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ReadWritePaths=/opt/rollforge/apps/hub-web/.next/cache",
        ),
    }
    for filename, required_directives in checks.items():
        try:
            content = (repo / "infra/systemd" / filename).read_text()
        except OSError:
            raise PreflightError(f"缺少 systemd unit：{filename}") from None
        directives = {line.strip() for line in content.splitlines() if line.strip()}
        if not set(required_directives).issubset(directives):
            raise PreflightError(f"{filename} 的 loopback/沙箱限制不符合部署基线")


def validate_environment(
    repo: Path,
    compose_path: Path,
    hub_path: Path,
    worker_path: Path,
    web_path: Path | None,
    runtime_path: Path | None,
    node_check=check_node_runtime,
):
    infrastructure = read_env(compose_path, private_mode=True)
    infra_allowed = {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "ROLLFORGE_POSTGRES_HOST_PORT",
        "ROLLFORGE_REDIS_HOST_PORT",
        "ROLLFORGE_MINIO_HOST_PORT",
        "ROLLFORGE_MINIO_CONSOLE_HOST_PORT",
    }
    reject_unknown(infrastructure, infra_allowed, compose_path)
    require(
        infrastructure,
        (
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
        ),
        compose_path,
    )
    for key in ("POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD"):
        weak_value = infrastructure[key].lower()
        if (
            len(infrastructure[key]) < 24
            or weak_value in {"rollforge", "rollforge-local-only"}
            or weak_value.startswith(("replace-with-", "change-me", "your-"))
        ):
            raise PreflightError(f"{compose_path} 中 {key} 必须使用独立随机强凭证")
    if infrastructure["POSTGRES_PASSWORD"] == infrastructure["MINIO_ROOT_PASSWORD"]:
        raise PreflightError("PostgreSQL 与 MinIO 必须使用不同凭证")

    hub = read_env(hub_path, private_mode=False)
    reject_unknown(
        hub,
        {
            "ROLLFORGE_ENVIRONMENT",
            "ROLLFORGE_DATABASE_URL",
            "ROLLFORGE_REDIS_URL",
            "ROLLFORGE_API_CREDENTIALS",
            "ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED",
            "ROLLFORGE_APPROVED_TASKS_FILE",
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY",
            "ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP",
        },
        hub_path,
    )
    reject_runtime_credentials(hub, hub_path)
    require(
        hub,
        (
            "ROLLFORGE_DATABASE_URL",
            "ROLLFORGE_API_CREDENTIALS",
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY",
            "ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED",
        ),
        hub_path,
    )
    validate_object_endpoint(hub, hub_path)
    if hub.get("ROLLFORGE_ENVIRONMENT") != "production":
        raise PreflightError("Hub 必须设置 ROLLFORGE_ENVIRONMENT=production")
    if hub.get("ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED", "false").lower() != "false":
        raise PreflightError("首次部署必须保持控制面写入关闭")
    known_credentials = credentials(hub["ROLLFORGE_API_CREDENTIALS"])
    user_hashes = {item["token_sha256"] for item in known_credentials if item["role"] == "USER"}
    worker_hashes = {item["token_sha256"] for item in known_credentials if item["role"] == "WORKER"}

    worker = read_env(worker_path, private_mode=False)
    reject_runtime_credentials(worker, worker_path)
    reject_unknown(
        worker,
        {
            "ROLLFORGE_ENVIRONMENT",
            "ROLLFORGE_WORKER_RUNS_ENABLED",
            "ROLLFORGE_WORKER_HUB_URL",
            "ROLLFORGE_WORKER_HUB_TOKEN",
            "ROLLFORGE_WORKER_RUNTIME_PYTHON",
            "ROLLFORGE_WORKER_RUNTIME_ENV_FILE",
            "ROLLFORGE_WORKER_GATEWAY_URL",
            "ROLLFORGE_WORKER_ALLOW_LAN_HTTP",
            "ROLLFORGE_WORKER_SPOOL",
            "ROLLFORGE_SCHEDULER_ENABLED",
            "ROLLFORGE_SCHEDULER_CONCURRENCY",
            "ROLLFORGE_SCHEDULER_POLL_SECONDS",
            "ROLLFORGE_SCHEDULER_RECOVERY_SECONDS",
            "ROLLFORGE_SCHEDULER_SHUTDOWN_SECONDS",
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY",
            "ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP",
        },
        worker_path,
    )
    require(
        worker,
        (
            "ROLLFORGE_WORKER_HUB_URL",
            "ROLLFORGE_WORKER_HUB_TOKEN",
            "ROLLFORGE_WORKER_RUNS_ENABLED",
            "ROLLFORGE_SCHEDULER_ENABLED",
            "ROLLFORGE_SCHEDULER_CONCURRENCY",
            "ROLLFORGE_OBJECT_STORAGE_ENDPOINT",
            "ROLLFORGE_OBJECT_STORAGE_BUCKET",
            "ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY",
            "ROLLFORGE_OBJECT_STORAGE_SECRET_KEY",
        ),
        worker_path,
    )
    validate_loopback_hub_url(
        worker["ROLLFORGE_WORKER_HUB_URL"], worker_path, "ROLLFORGE_WORKER_HUB_URL"
    )
    validate_object_endpoint(worker, worker_path)
    if (
        worker["ROLLFORGE_OBJECT_STORAGE_ENDPOINT"] != hub["ROLLFORGE_OBJECT_STORAGE_ENDPOINT"]
        or worker["ROLLFORGE_OBJECT_STORAGE_BUCKET"] != hub["ROLLFORGE_OBJECT_STORAGE_BUCKET"]
    ):
        raise PreflightError("Hub 与 Worker 必须使用同一对象存储 endpoint 和 bucket")
    if (
        worker["ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY"] == hub["ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY"]
        or worker["ROLLFORGE_OBJECT_STORAGE_SECRET_KEY"]
        == hub["ROLLFORGE_OBJECT_STORAGE_SECRET_KEY"]
    ):
        raise PreflightError("Hub 与 Worker 必须使用不同的对象存储凭证")
    if worker.get("ROLLFORGE_WORKER_RUNS_ENABLED", "false").lower() != "false":
        raise PreflightError("首次部署必须保持 Worker 执行关闭")
    if worker.get("ROLLFORGE_SCHEDULER_ENABLED", "false").lower() != "false":
        raise PreflightError("首次部署必须保持 Scheduler 关闭")
    if worker.get("ROLLFORGE_SCHEDULER_CONCURRENCY", "1") != "1":
        raise PreflightError("首轮部署 Scheduler 并发必须为 1")
    worker_token = worker.get("ROLLFORGE_WORKER_HUB_TOKEN", "")
    if worker_token and hashlib.sha256(worker_token.encode()).hexdigest() not in worker_hashes:
        raise PreflightError("Worker Token 与 Hub 的 WORKER 凭证不匹配")
    if worker_token and len(worker_token) < 32:
        raise PreflightError("Worker Hub Token 长度不足")

    if web_path:
        web = read_env(web_path, private_mode=False)
        reject_runtime_credentials(web, web_path)
        if any(key.startswith("NEXT_PUBLIC_") for key in web):
            raise PreflightError("Web 环境禁止使用 NEXT_PUBLIC_ 配置")
        reject_unknown(
            web,
            {
                "ROLLFORGE_API_URL",
                "ROLLFORGE_WEB_USER_TOKEN",
                "ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS",
                "ROLLFORGE_WEB_HOST_PORT",
            },
            web_path,
        )
        require(
            web,
            (
                "ROLLFORGE_API_URL",
                "ROLLFORGE_WEB_USER_TOKEN",
                "ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS",
                "ROLLFORGE_WEB_HOST_PORT",
            ),
            web_path,
        )
        if len(web["ROLLFORGE_WEB_USER_TOKEN"]) < 32:
            raise PreflightError("Web USER Token 长度不足")
        validate_loopback_hub_url(web["ROLLFORGE_API_URL"], web_path, "ROLLFORGE_API_URL")
        if hashlib.sha256(web["ROLLFORGE_WEB_USER_TOKEN"].encode()).hexdigest() not in user_hashes:
            raise PreflightError("Web USER Token 与 Hub 的 USER 凭证不匹配")
        if web.get("ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS", "false").lower() != "false":
            raise PreflightError("首次部署必须保持 Web 实验写入关闭")
        try:
            web_port = int(web["ROLLFORGE_WEB_HOST_PORT"])
        except ValueError:
            raise PreflightError("ROLLFORGE_WEB_HOST_PORT 必须是有效 TCP 端口") from None
        if not 1024 <= web_port <= 65535 or web_port == 8000:
            raise PreflightError("ROLLFORGE_WEB_HOST_PORT 无效或与 Hub 端口冲突")
        node_check()
    else:
        web_port = 3300

    if runtime_path:
        runtime = read_env(runtime_path, private_mode=True)
        reject_unknown(
            runtime,
            {
                "ROLLFORGE_MODEL_SESSION_TOKEN",
                "E2B_API_KEY",
                "E2B_API_URL",
                "E2B_SANDBOX_URL",
                "E2B_DOMAIN",
            },
            runtime_path,
        )
        require(runtime, ("ROLLFORGE_MODEL_SESSION_TOKEN", "E2B_API_KEY"), runtime_path)
        if {"ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}.intersection(runtime):
            raise PreflightError("Runtime 配置禁止放入上游模型 API Key")
        parent_mode = stat.S_IMODE(runtime_path.parent.stat().st_mode)
        if parent_mode != 0o700:
            raise PreflightError("Runtime 私密目录权限必须为 0700")

    return hub, worker, web_port


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--compose-env", type=Path, required=True)
    parser.add_argument("--hub-env", type=Path, required=True)
    parser.add_argument("--worker-env", type=Path, required=True)
    parser.add_argument("--web-env", type=Path)
    parser.add_argument("--runtime-env", type=Path)
    args = parser.parse_args()
    try:
        validate_units(args.repo)
        _hub, _worker, web_port = validate_environment(
            args.repo,
            args.compose_env,
            args.hub_env,
            args.worker_env,
            args.web_env,
            args.runtime_env,
        )
        config = compose_config(args.repo, args.compose_env)
        validate_compose_config(config, web_port=web_port)
    except PreflightError as exc:
        print(f"RollForge deployment preflight: FAILED ({exc})", file=sys.stderr)
        return 1
    print("RollForge deployment preflight: PASS; all write and execution gates are closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
