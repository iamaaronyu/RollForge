"""Linux E2B 重启恢复：默认只读，--apply 需 root；不删除数据或重启主机。"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from check_e2b_host import inspect_host

COMPOSE_SHA256 = "eab149fa2908276f2c8764a72fa2c1ff21958a1c7877d5fdd1f7fc622e783c36"
GUARD = [
    "INPUT",
    "!",
    "-i",
    "lo",
    "-p",
    "tcp",
    "--dport",
    "5008",
    "-m",
    "comment",
    "--comment",
    "rollforge-e2b-control",
    "-j",
    "REJECT",
]


def read_report(directory: Path, allow_low_memory: bool) -> dict:
    report = inspect_host(allow_low_memory)
    compose = directory / "compose.yaml"
    pinned = (
        compose.is_file() and hashlib.sha256(compose.read_bytes()).hexdigest() == COMPOSE_SHA256
    )
    if not pinned:
        report["errors"].append("Compose 不匹配固定版本 SHA256，拒绝恢复")
    if not (directory / ".env").is_file():
        report["errors"].append("部署目录缺少 .env")
    if report["system"] == "Linux":
        report["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(("HugePages_Total:", "HugePages_Free:")):
                name, value = line.split(":", 1)
                values[name] = int(value.strip())
        report["hugepages"] = values
    report["compose_pinned"] = pinned
    report["mutated"] = False
    return report


def apply(directory: Path) -> Path:
    if os.geteuid() != 0:
        raise PermissionError("恢复需要 owner 在 Linux 终端使用 sudo")
    # 非零 1 才表示不存在；检查错误不能当成无活动 VM。
    active = subprocess.run(["pgrep", "-x", "firecracker"], capture_output=True, check=False)
    if active.returncode != 1:
        raise RuntimeError("存在 Firecracker 或无法确认活动 VM 状态，拒绝恢复")
    log = directory / f"recovery-{uuid4().hex}.local.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        # 先补控制端口防护，再执行官方宿主初始化和服务恢复。
        guard = subprocess.run(
            ["iptables", "-w", "-C", *GUARD], stdout=stream, stderr=stream, check=False
        )
        if guard.returncode == 1:
            subprocess.run(
                ["iptables", "-w", "-I", "INPUT", "1", *GUARD[1:]],
                stdout=stream,
                stderr=stream,
                check=True,
            )
        elif guard.returncode != 0:
            raise RuntimeError("无法检查控制端口防护，拒绝启动")
        for command in (
            ["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "600"],
            ["docker", "compose", "--profile", "test", "run", "--rm", "-T", "smoke"],
        ):
            subprocess.run(command, cwd=directory, stdout=stream, stderr=stream, check=True)
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-dir", type=Path, required=True)
    parser.add_argument("--allow-low-memory", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        directory = args.deployment_dir.resolve(strict=True)
        os.chdir(directory)
        report = read_report(directory, args.allow_low_memory)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if report["errors"]:
            return 2
        if args.apply:
            apply(directory)
            print(
                json.dumps(
                    {"status": "services_and_sdk_smoke_passed", "host_reboot_validated": False}
                )
            )
        return 0
    except Exception as exc:
        # Compose/SDK 原始日志可能包含密钥，只报告异常类型。
        print(json.dumps({"status": "recovery_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
