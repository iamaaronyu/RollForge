"""Apply the narrow host firewall rules for the dedicated RollForge E2B gateway."""

import argparse
import ipaddress
import subprocess
import sys

TAG = "rollforge-model-gateway"
SOURCE_RANGE = "10.11.0.1-10.11.0.33"
GATEWAY_PORT = "11441"


def gateway_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("网关地址必须是单个 Tailnet IPv4 地址") from error
    if not isinstance(address, ipaddress.IPv4Address) or address not in ipaddress.ip_network(
        "100.64.0.0/10"
    ):
        raise ValueError("网关地址必须是单个 Tailnet IPv4 地址")
    return f"{address}/32"


def rules_for_gateway(address: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    target = gateway_address(address)
    comment = ("-m", "comment", "--comment", TAG)
    return (
        (
            "raw",
            (
                "-i",
                "veth+",
                "-d",
                target,
                "-p",
                "tcp",
                "--dport",
                GATEWAY_PORT,
                *comment,
                "-j",
                "ACCEPT",
            ),
        ),
        (
            "raw",
            ("-i", "veth+", "-d", target, *comment, "-j", "DROP"),
        ),
        (
            "nat",
            (
                "-i",
                "veth+",
                "-d",
                target,
                "-p",
                "tcp",
                "--dport",
                GATEWAY_PORT,
                *comment,
                "-j",
                "ACCEPT",
            ),
        ),
        (
            "nat",
            (
                "-m",
                "iprange",
                "--src-range",
                SOURCE_RANGE,
                "-o",
                "tailscale0",
                "-d",
                target,
                "-p",
                "tcp",
                "--dport",
                GATEWAY_PORT,
                *comment,
                "-j",
                "MASQUERADE",
            ),
        ),
    )


def iptables(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["iptables", "-w", "5", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def chain_for(table: str, rule: tuple[str, ...]) -> str:
    return "POSTROUTING" if table == "nat" and "-o" in rule else "PREROUTING"


def insertion_position(table: str, rule: tuple[str, ...]) -> str:
    if table == "raw" and "DROP" in rule:
        return "2"
    return "1"


def ensure_rule(table: str, rule: tuple[str, ...]) -> bool:
    chain = chain_for(table, rule)
    check = iptables("-t", table, "-C", chain, *rule)
    if check.returncode == 0:
        return False
    if check.returncode != 1:
        raise RuntimeError("无法检查现有防火墙规则")

    inserted = iptables("-t", table, "-I", chain, insertion_position(table, rule), *rule)
    if inserted.returncode:
        raise RuntimeError("无法安装受限网关防火墙规则")
    return True


def remove_rule(table: str, rule: tuple[str, ...]) -> bool:
    chain = chain_for(table, rule)
    check = iptables("-t", table, "-C", chain, *rule)
    if check.returncode == 1:
        return False
    if check.returncode != 0:
        raise RuntimeError("无法检查现有防火墙规则")
    deleted = iptables("-t", table, "-D", chain, *rule)
    if deleted.returncode:
        raise RuntimeError("无法删除受限网关防火墙规则")
    return True


def apply(address: str) -> int:
    added: list[tuple[str, tuple[str, ...]]] = []
    try:
        for table, rule in rules_for_gateway(address):
            if ensure_rule(table, rule):
                added.append((table, rule))
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        for table, rule in reversed(added):
            try:
                remove_rule(table, rule)
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                pass
        raise
    return len(added)


def remove(address: str) -> int:
    removed = 0
    for table, rule in reversed(rules_for_gateway(address)):
        removed += remove_rule(table, rule)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "remove"))
    parser.add_argument("--gateway-ip", required=True)
    arguments = parser.parse_args()
    if not hasattr(sys, "platform") or sys.platform != "linux":
        parser.error("该工具只可在 E2B Linux 宿主上运行")
    try:
        if arguments.action == "apply":
            changed = apply(arguments.gateway_ip)
        else:
            changed = remove(arguments.gateway_ip)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"firewall_{arguments.action}=failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"firewall_{arguments.action}=ok rules_changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
