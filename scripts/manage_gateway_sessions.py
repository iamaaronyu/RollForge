"""安全签发和撤销模型网关的短时会话。"""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path

MODEL_ID = "deepseek-flash"
MAX_TTL_SECONDS = 3600
MAX_CONFIG_BYTES = 1024 * 1024
MICROUSD_PER_USD = 1_000_000
MAX_SESSION_COST_MICROUSD = 1_000_000_000


class SessionConfigError(ValueError):
    """私密会话配置不安全或格式不正确。"""


def _check_private_path(config: Path):
    parent = config.parent
    try:
        parent_info = parent.lstat()
        config_info = config.lstat()
    except OSError:
        raise SessionConfigError("会话配置文件或私密目录不可访问") from None
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_IMODE(parent_info.st_mode) != 0o700
        or parent_info.st_uid != os.geteuid()
    ):
        raise SessionConfigError("会话配置父目录必须是私有目录（权限 0700）")
    if (
        not stat.S_ISREG(config_info.st_mode)
        or stat.S_IMODE(config_info.st_mode) != 0o600
        or config_info.st_uid != os.geteuid()
    ):
        raise SessionConfigError("会话配置必须是权限 0600 的普通文件")
    if config_info.st_size > MAX_CONFIG_BYTES:
        raise SessionConfigError("会话配置文件超过大小限制")
    return config_info


@contextmanager
def _locked_config(config: Path):
    config = config.expanduser()
    _check_private_path(config)
    lock_path = config.with_name(config.name + ".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        raise SessionConfigError("无法打开私密会话配置锁") from None
    try:
        lock_info = os.fstat(fd)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or stat.S_IMODE(lock_info.st_mode) != 0o600
            or lock_info.st_uid != os.geteuid()
        ):
            raise SessionConfigError("会话配置锁必须是权限 0600 的普通文件")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            config_info = _check_private_path(config)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                config_fd = os.open(config, flags)
                with os.fdopen(config_fd, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise SessionConfigError("会话配置无法安全读取") from None
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("upstream_key"), str)
                or not data["upstream_key"]
                or not isinstance(data.get("sessions"), dict)
            ):
                raise SessionConfigError("会话配置结构无效")
            yield data, config_info
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_config(config: Path, data: dict, original_info):
    try:
        current_info = config.lstat()
    except OSError:
        raise SessionConfigError("会话配置在更新期间消失") from None
    if (
        current_info.st_dev != original_info.st_dev
        or current_info.st_ino != original_info.st_ino
        or current_info.st_mtime_ns != original_info.st_mtime_ns
        or current_info.st_size != original_info.st_size
    ):
        raise SessionConfigError("会话配置在更新期间发生变化，请重试")

    fd, temporary_name = tempfile.mkstemp(prefix=".gateway-session-", dir=config.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        stream_fd = fd
        fd = -1
        with os.fdopen(stream_fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config)
        directory_fd = os.open(config.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        raise SessionConfigError("会话配置安全更新失败") from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def cost_to_microusd(value: str) -> int:
    try:
        amount = Decimal(value)
        scaled = amount * MICROUSD_PER_USD
    except (DecimalException, InvalidOperation, ValueError):
        raise SessionConfigError("费用上限必须是有效的美元金额") from None
    if not amount.is_finite() or amount <= 0 or scaled != scaled.to_integral_value():
        raise SessionConfigError("费用上限须为正数且最多有 6 位小数")
    if scaled > MAX_SESSION_COST_MICROUSD:
        raise SessionConfigError("单会话费用上限不能超过 1000 美元")
    return int(scaled)


def issue_session(config: Path, ttl_seconds: int, max_requests: int, max_cost_microusd: int):
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
    ):
        raise SessionConfigError(f"会话有效期必须在 1 至 {MAX_TTL_SECONDS} 秒之间")
    if (
        not isinstance(max_requests, int)
        or isinstance(max_requests, bool)
        or not 1 <= max_requests <= 1000
    ):
        raise SessionConfigError("请求数上限必须在 1 至 1000 之间")
    if (
        not isinstance(max_cost_microusd, int)
        or isinstance(max_cost_microusd, bool)
        or not 0 < max_cost_microusd <= MAX_SESSION_COST_MICROUSD
    ):
        raise SessionConfigError("费用上限必须是 0 至 1000 美元范围内的正微美元整数")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _locked_config(config) as (data, original_info):
        if token_hash in data["sessions"]:
            raise SessionConfigError("会话标识冲突，请重试")
        data["sessions"][token_hash] = {
            "model": MODEL_ID,
            "expires_at": time.time() + ttl_seconds,
            "max_requests": max_requests,
            "max_cost_microusd": max_cost_microusd,
        }
        _write_config(config.expanduser(), data, original_info)
    return token


def revoke_session(config: Path, token: str) -> bool:
    token = token.strip()
    if not token or len(token) > 256:
        raise SessionConfigError("需要通过标准输入提供有效会话 Token")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _locked_config(config) as (data, original_info):
        if token_hash not in data["sessions"]:
            return False
        del data["sessions"][token_hash]
        _write_config(config.expanduser(), data, original_info)
    return True


def revoke_all(config: Path) -> int:
    with _locked_config(config) as (data, original_info):
        count = len(data["sessions"])
        if count:
            data["sessions"] = {}
            _write_config(config.expanduser(), data, original_info)
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description="管理本机模型网关的短时会话")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="签发一次性显示的限额会话 Token")
    issue.add_argument("--config", type=Path, required=True)
    issue.add_argument("--ttl-seconds", type=int, required=True)
    issue.add_argument("--max-requests", type=int, required=True)
    issue.add_argument("--max-cost-usd", required=True)
    revoke = commands.add_parser("revoke", help="从标准输入读取 Token 并撤销单个会话")
    revoke.add_argument("--config", type=Path, required=True)
    revoke_all_command = commands.add_parser("revoke-all", help="撤销配置中的全部会话")
    revoke_all_command.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            token = issue_session(
                args.config,
                args.ttl_seconds,
                args.max_requests,
                cost_to_microusd(args.max_cost_usd),
            )
            print(token)
        elif args.command == "revoke":
            token = sys.stdin.read(257)
            if revoke_session(args.config, token):
                print("会话已撤销")
            else:
                print("会话不存在", file=sys.stderr)
                return 1
        else:
            print(f"已撤销 {revoke_all(args.config)} 个会话")
    except SessionConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
