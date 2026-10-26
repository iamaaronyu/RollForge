"""Safely start RollForge's isolated PostgreSQL, Redis, and MinIO services."""

import argparse
import subprocess
import sys
from pathlib import Path

from check_deployment import (
    PreflightError,
    compose_config,
    compose_process_environment,
    validate_compose_config,
    validate_infrastructure_environment,
)


def start(repo: Path, env_file: Path) -> None:
    validate_infrastructure_environment(env_file)
    config = compose_config(repo, env_file)
    validate_compose_config(config)
    command = [
        "docker",
        "compose",
        "--project-name",
        "rollforge",
        "--env-file",
        str(env_file),
        "--file",
        str(repo / "infra/docker-compose/compose.yaml"),
        "up",
        "-d",
    ]
    result = subprocess.run(command, check=False, cwd=repo, env=compose_process_environment())
    if result.returncode:
        raise PreflightError("RollForge 数据服务启动失败；检查 Docker 状态和服务日志")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    try:
        start(repo, args.env_file)
    except PreflightError as exc:
        print(f"部署预检未通过：{exc}", file=sys.stderr)
        return 2
    print("RollForge PostgreSQL、Redis、MinIO 已启动；Hub/Worker/Scheduler 未启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
