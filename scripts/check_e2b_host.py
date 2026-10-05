"""只读检查 E2B Embed Linux 宿主；不会修改内核、网络或软件。"""

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


def command(*args: str) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def version(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else (0, 0, 0)


def inspect_host() -> dict:
    system, arch = platform.system(), platform.machine()
    errors, recommendations = [], []
    memory_gib = None
    if system != "Linux":
        errors.append("请在目标 Linux 主机运行；当前主机不通过 Linux KVM 检查")
    else:
        meminfo = Path("/proc/meminfo").read_text()
        memory_gib = int(re.search(r"MemTotal:\s+(\d+)", meminfo)[1]) / 1024**2
        if arch not in {"x86_64", "aarch64", "arm64"}:
            errors.append("架构须为 x86_64 或 ARM64")
        if os.sysconf("SC_PAGE_SIZE") != 4096:
            errors.append("内核页大小须为 4 KiB")
        minimum = (6, 10, 0) if arch in {"aarch64", "arm64"} else (6, 8, 0)
        if version(platform.release()) < minimum:
            errors.append("内核低于项目测试基线：x86_64 6.8 / ARM64 6.10")
        for device in ("/dev/kvm", "/dev/net/tun"):
            if not Path(device).exists():
                errors.append(f"缺少设备 {device}")
        if not Path("/sys/fs/cgroup/cgroup.controllers").exists():
            errors.append("需要 cgroup v2")
    free_gib = shutil.disk_usage(Path.cwd()).free / 1024**3
    docker = command("docker", "--version")
    compose = command("docker", "compose", "version", "--short")
    daemon = command("docker", "info", "--format", "{{.ServerVersion}}")
    if version(docker) < (27, 0, 0):
        errors.append("需要 Docker Engine 27+")
    if version(compose) < (2, 24, 0):
        errors.append("需要 Docker Compose 2.24+")
    if not daemon:
        errors.append("Docker daemon 未就绪或当前用户无权限")
    if memory_gib is not None and memory_gib < 12:
        errors.append("可用主机内存低于官方建议的 12 GiB")
    if free_gib < 20:
        errors.append("空闲磁盘低于 20 GiB")
    if (os.cpu_count() or 0) < 4 or (memory_gib or 0) < 16 or free_gib < 60:
        recommendations.append("本项目首次测试建议 4–8 vCPU、16 GiB 内存、60 GiB 空闲 SSD")
    return {
        "system": system,
        "architecture": arch,
        "kernel": platform.release(),
        "cpu_count": os.cpu_count(),
        "memory_gib": round(memory_gib, 2) if memory_gib is not None else None,
        "free_disk_gib": round(free_gib, 2),
        "docker": docker,
        "compose": compose,
        "errors": errors,
        "recommendations": recommendations,
        "real_e2b_started": False,
    }


if __name__ == "__main__":
    report = inspect_host()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report["errors"] else 0)
