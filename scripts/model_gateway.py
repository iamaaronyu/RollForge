"""S0 Anthropic 网关：上游密钥留在可信宿主，限制会话模型、次数、成本与有效期。"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

UPSTREAM = "https://api.deepseek.com/anthropic"
MAX_BODY = 2 * 1024 * 1024
MODEL_CONTEXT_TOKENS = 1_000_000
MICROUSD_PER_MILLION_INPUT_TOKENS = 300_000
MICROUSD_PER_MILLION_OUTPUT_TOKENS = 1_200_000
MICROUSD_PER_SEARCH_FETCH = 750
MICROUSD_DENOMINATOR = 1_000_000
MAX_OBSERVED_JSON_RESPONSE = 8 * 1024 * 1024


@dataclass(frozen=True)
class Authorization:
    upstream_key: str
    token_sha256: str
    reserved_cost_microusd: int


def cost_microusd(input_tokens: int, output_tokens: int, server_tool_calls: int = 0) -> int:
    numerator = (
        input_tokens * MICROUSD_PER_MILLION_INPUT_TOKENS
        + output_tokens * MICROUSD_PER_MILLION_OUTPUT_TOKENS
    )
    token_cost = (numerator + MICROUSD_DENOMINATOR - 1) // MICROUSD_DENOMINATOR
    return token_cost + server_tool_calls * MICROUSD_PER_SEARCH_FETCH


def valid_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def request_cost_reservation(payload: dict) -> int:
    max_tokens = payload["max_tokens"]
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("invalid tool list")
    if any(not isinstance(tool, dict) for tool in tools):
        raise ValueError("invalid tool list")
    server_tool_types = []
    for tool in tools:
        tool_type = tool.get("type", "")
        if not isinstance(tool_type, str):
            raise ValueError("invalid server tool type")
        server_tool_types.append(tool_type)
    search_fetch_tools = [
        tool_type
        for tool_type in server_tool_types
        if tool_type.startswith(("web_search", "web_fetch"))
    ]
    unsupported_server_tools = [
        tool_type
        for tool_type in server_tool_types
        if tool_type.startswith(("web_", "server_"))
        and not tool_type.startswith(("web_search", "web_fetch"))
    ]
    if unsupported_server_tools:
        raise ValueError("unsupported billable server tool")
    # Reserve the full model context at peak, cache-miss rates before inference.
    # This deliberately over-reserves; a complete provider usage record releases
    # the difference after the response has been streamed.
    server_tool_bound = max_tokens if search_fetch_tools else 0
    # Anthropic-compatible usage can report cache tokens separately from
    # input_tokens; reserve twice the context bound to cover either convention.
    return cost_microusd(MODEL_CONTEXT_TOKENS * 2, max_tokens, server_tool_bound)


class UsageObserver:
    def __init__(self, content_type: str):
        self.streaming = "text/event-stream" in content_type.lower()
        self.buffer = bytearray()
        self.input_tokens = None
        self.output_tokens = None
        self.cached_tokens = 0
        self.server_tool_calls = 0
        self.message_stopped = False
        self.invalid = False

    def _record(self, event: dict):
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message")
            usage = message.get("usage", {}) if isinstance(message, dict) else {}
            if not isinstance(usage, dict):
                self.invalid = True
                return
            input_tokens = usage.get("input_tokens")
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_created = usage.get("cache_creation_input_tokens", 0)
            if (
                not valid_count(input_tokens)
                or not valid_count(cache_read)
                or not valid_count(cache_created)
            ):
                self.invalid = True
                return
            self.input_tokens = input_tokens
            self.cached_tokens += cache_read + cache_created
        elif event_type == "message_delta":
            usage = event.get("usage", {})
            if not isinstance(usage, dict):
                self.invalid = True
                return
            if "output_tokens" in usage:
                if not valid_count(usage["output_tokens"]):
                    self.invalid = True
                    return
                self.output_tokens = usage["output_tokens"]
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_created = usage.get("cache_creation_input_tokens", 0)
            if not valid_count(cache_read) or not valid_count(cache_created):
                self.invalid = True
                return
            self.cached_tokens += cache_read + cache_created
        elif event_type == "content_block_start":
            content_block = event.get("content_block", {})
            if isinstance(content_block, dict) and content_block.get("type") == "server_tool_use":
                self.server_tool_calls += 1
        elif event_type == "message_stop":
            self.message_stopped = True

    def _record_sse_event(self, raw_event: bytes):
        data_lines = [
            line[5:].strip() for line in raw_event.splitlines() if line.startswith(b"data:")
        ]
        if not data_lines:
            return
        try:
            value = json.loads(b"\n".join(data_lines))
            if isinstance(value, dict):
                try:
                    self._record(value)
                except (AttributeError, TypeError, ValueError):
                    self.invalid = True
            else:
                self.invalid = True
        except (ValueError, TypeError):
            self.invalid = True

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        if self.streaming:
            self.buffer = bytearray(self.buffer.replace(b"\r\n", b"\n"))
            while b"\n\n" in self.buffer:
                raw_event, remainder = self.buffer.split(b"\n\n", 1)
                self.buffer = bytearray(remainder)
                self._record_sse_event(raw_event)
            if len(self.buffer) > MAX_BODY:
                self.invalid = True
                self.buffer.clear()
        elif len(self.buffer) > MAX_OBSERVED_JSON_RESPONSE:
            self.invalid = True
            self.buffer.clear()

    def finish(self) -> int | None:
        if self.streaming:
            if self.buffer:
                self._record_sse_event(bytes(self.buffer))
            complete = self.message_stopped
        else:
            try:
                value = json.loads(self.buffer)
                if not isinstance(value, dict) or value.get("type") != "message":
                    self.invalid = True
                else:
                    usage = value.get("usage", {})
                    if not isinstance(usage, dict):
                        self.invalid = True
                        return None
                    self.input_tokens = usage.get("input_tokens")
                    self.output_tokens = usage.get("output_tokens")
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_created = usage.get("cache_creation_input_tokens", 0)
                    if (
                        not valid_count(self.input_tokens)
                        or not valid_count(self.output_tokens)
                        or not valid_count(cache_read)
                        or not valid_count(cache_created)
                    ):
                        self.invalid = True
                        return None
                    self.cached_tokens = cache_read + cache_created
                    self.server_tool_calls = sum(
                        1
                        for block in value.get("content", [])
                        if isinstance(block, dict) and block.get("type") == "server_tool_use"
                    )
                    complete = True
            except (ValueError, TypeError):
                self.invalid = True
                complete = False

        counts = (self.input_tokens, self.output_tokens, self.cached_tokens, self.server_tool_calls)
        if self.invalid or not complete or any(not valid_count(count) for count in counts):
            return None
        return cost_microusd(
            self.input_tokens + self.cached_tokens,
            self.output_tokens,
            self.server_tool_calls,
        )


class Sessions:
    def __init__(self, config: Path):
        self.config = config
        self.state_db = config.with_suffix(".sqlite3")
        self.lock = threading.Lock()

    def _initialize_budget_store(self):
        info = self.config.parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("网关配置目录必须为仅 owner 可访问的目录")
        descriptor = os.open(
            self.state_db, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with sqlite3.connect(self.state_db, timeout=30) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS session_usage ("
                "token_sha256 TEXT PRIMARY KEY, "
                "used_requests INTEGER NOT NULL CHECK (used_requests >= 0), "
                "used_cost_microusd INTEGER NOT NULL DEFAULT 0 "
                "CHECK (used_cost_microusd >= 0), "
                "reserved_cost_microusd INTEGER NOT NULL DEFAULT 0 "
                "CHECK (reserved_cost_microusd >= 0))"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(session_usage)")}
            if "reserved_cost_microusd" not in columns:
                connection.execute(
                    "ALTER TABLE session_usage ADD COLUMN reserved_cost_microusd "
                    "INTEGER NOT NULL DEFAULT 0 CHECK (reserved_cost_microusd >= 0)"
                )
            if "used_cost_microusd" not in columns:
                connection.execute(
                    "ALTER TABLE session_usage ADD COLUMN used_cost_microusd "
                    "INTEGER NOT NULL DEFAULT 0 CHECK (used_cost_microusd >= 0)"
                )
        self.state_db.chmod(0o600)

    def initialize(self):
        info = self.config.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("网关配置必须为仅 owner 可读写的普通文件")
        try:
            data = json.loads(self.config.read_text())
        except (OSError, json.JSONDecodeError):
            raise ValueError("网关配置无效") from None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("upstream_key"), str)
            or not data["upstream_key"]
            or not isinstance(data.get("sessions", {}), dict)
        ):
            raise ValueError("网关配置结构无效")
        self._initialize_budget_store()

    def authorize(
        self, token: str, model: str, cost_reservation_microusd: int
    ) -> Authorization | None:
        # 会话按 token 摘要索引；请求与最高成本预留在 SQLite 事务中原子扣减。
        # 重新读取配置使删除或修改会话立即撤销；仅返回上游 Key 给宿主 HTTP 层。
        if (
            not isinstance(cost_reservation_microusd, int)
            or isinstance(cost_reservation_microusd, bool)
            or cost_reservation_microusd < 0
        ):
            raise ValueError("cost reservation is invalid")
        with self.lock:
            info = self.config.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("网关配置必须为仅 owner 可读写的普通文件")
            data = json.loads(self.config.read_text())
            identity = hashlib.sha256(token.encode()).hexdigest()
            session = data.get("sessions", {}).get(identity)
            if not token or not session:
                return None
            if (
                not isinstance(session, dict)
                or not isinstance(session.get("max_requests"), int)
                or isinstance(session.get("max_requests"), bool)
                or session["max_requests"] < 1
                or not isinstance(session.get("max_cost_microusd"), int)
                or isinstance(session.get("max_cost_microusd"), bool)
                or session["max_cost_microusd"] < 0
                or not isinstance(session.get("expires_at"), (int, float))
                or isinstance(session.get("expires_at"), bool)
                or (
                    isinstance(session.get("expires_at"), float)
                    and not math.isfinite(session["expires_at"])
                )
                or time.time() >= session.get("expires_at", 0)
                or model != session.get("model")
            ):
                return None
            self._initialize_budget_store()
            with sqlite3.connect(self.state_db, timeout=30) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT used_requests, used_cost_microusd, reserved_cost_microusd "
                    "FROM session_usage "
                    "WHERE token_sha256 = ?",
                    (identity,),
                ).fetchone()
                used_requests, used_cost, reserved_cost = row if row is not None else (0, 0, 0)
                if (
                    used_requests >= session["max_requests"]
                    or used_cost + reserved_cost + cost_reservation_microusd
                    > session["max_cost_microusd"]
                ):
                    return None
                connection.execute(
                    "INSERT INTO session_usage(token_sha256, used_requests, used_cost_microusd, "
                    "reserved_cost_microusd) VALUES (?, 1, 0, ?) "
                    "ON CONFLICT(token_sha256) DO UPDATE "
                    "SET used_requests = session_usage.used_requests + 1, "
                    "reserved_cost_microusd = session_usage.reserved_cost_microusd + "
                    "excluded.reserved_cost_microusd",
                    (identity, cost_reservation_microusd),
                )
            return Authorization(data["upstream_key"], identity, cost_reservation_microusd)

    def settle(self, authorization: Authorization, actual_cost_microusd: int):
        if actual_cost_microusd < 0:
            raise ValueError("model usage cost cannot be negative")
        with sqlite3.connect(self.state_db, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE session_usage SET "
                "reserved_cost_microusd = reserved_cost_microusd - ?, "
                "used_cost_microusd = used_cost_microusd + ? "
                "WHERE token_sha256 = ? AND reserved_cost_microusd >= ?",
                (
                    authorization.reserved_cost_microusd,
                    actual_cost_microusd,
                    authorization.token_sha256,
                    authorization.reserved_cost_microusd,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("model usage reservation is missing")


def make_handler(sessions: Sessions):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 通过连接关闭结束 SSE，不缓存整条流，不记录正文或认证头。
        def log_message(self, *_args):
            pass

        def reply(self, status: int, message: str):
            print(json.dumps({"gateway_status": status}), flush=True)
            body = json.dumps({"error": {"type": "gateway_error", "message": message}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # Claude Code 使用 Anthropic SDK 的 beta=true 查询参数。
            # 只允许这两个固定入口，不把客户端 URL 拼接进上游地址。
            if self.path not in {"/v1/messages", "/v1/messages?beta=true"}:
                self.reply(404, "unsupported endpoint")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                    self.reply(413, "invalid request size")
                    return
                body = self.rfile.read(size)
                payload = json.loads(body)
                # 防止会话利用任意模型或无限输出增加费用。
                if (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("max_tokens"), int)
                    or isinstance(payload["max_tokens"], bool)
                    or not 1 <= payload["max_tokens"] <= 8192
                ):
                    self.reply(400, "invalid token budget")
                    return
                try:
                    reservation = request_cost_reservation(payload)
                except ValueError:
                    self.reply(400, "unsupported server tool")
                    return
                authorization = sessions.authorize(
                    self.headers.get("x-api-key", ""), payload.get("model"), reservation
                )
                if authorization is None:
                    self.reply(403, "session rejected")
                    return
                request = Request(
                    UPSTREAM + "/v1/messages",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": authorization.upstream_key,
                        "anthropic-version": "2023-06-01",
                    },
                    method="POST",
                )
                try:
                    upstream = build_opener(ProxyHandler({})).open(request, timeout=60)
                except HTTPError as exc:
                    # 不转发上游错误正文：它可能包含客户端输入或服务端认证信息。
                    self.reply(exc.code, "upstream rejected request")
                    return
                with upstream:
                    observer = UsageObserver(
                        upstream.headers.get("Content-Type", "application/json")
                    )
                    print(json.dumps({"gateway_status": upstream.status}), flush=True)
                    self.send_response(upstream.status)
                    self.send_header(
                        "Content-Type", upstream.headers.get("Content-Type", "application/json")
                    )
                    self.end_headers()
                    while chunk := upstream.read1(65536):
                        observer.feed(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    actual_cost = observer.finish()
                    if actual_cost is not None:
                        sessions.settle(authorization, actual_cost)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                # 不将异常字符串、请求或配置写入 HTTP 错误或日志。
                self.close_connection = True
                try:
                    self.reply(502, "gateway request failed")
                except OSError:
                    pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11441)
    args = parser.parse_args()
    os.umask(0o077)
    sessions = Sessions(args.config)
    sessions.initialize()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(sessions))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
