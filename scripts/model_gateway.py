"""S0 Anthropic 网关：上游密钥留在可信宿主，限制会话模型、次数与有效期。"""

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

UPSTREAM = "https://api.deepseek.com/anthropic"
MAX_BODY = 2 * 1024 * 1024


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
                "used_requests INTEGER NOT NULL CHECK (used_requests >= 0))"
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

    def authorize(self, token: str, model: str) -> str | None:
        # 会话按 token 摘要索引；额度在 SQLite 事务中原子扣减并跨进程/重启持久化。
        # 重新读取配置使删除或修改会话立即撤销；仅返回上游 Key 给宿主 HTTP 层。
        with self.lock:
            info = self.config.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("网关配置必须为仅 owner 可读写的普通文件")
            data = json.loads(self.config.read_text())
            identity = hashlib.sha256(token.encode()).hexdigest()
            session = data.get("sessions", {}).get(identity)
            if not token or not session:
                return None
            if time.time() >= session["expires_at"] or model != session["model"]:
                return None
            self._initialize_budget_store()
            with sqlite3.connect(self.state_db, timeout=30) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT used_requests FROM session_usage WHERE token_sha256 = ?", (identity,)
                ).fetchone()
                used = row[0] if row is not None else 0
                if used >= session["max_requests"]:
                    return None
                connection.execute(
                    "INSERT INTO session_usage(token_sha256, used_requests) VALUES (?, 1) "
                    "ON CONFLICT(token_sha256) DO UPDATE "
                    "SET used_requests = excluded.used_requests + ?",
                    (identity, used),
                )
            return data["upstream_key"]


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
                if not isinstance(payload, dict) or not 1 <= payload.get("max_tokens", 0) <= 8192:
                    self.reply(400, "invalid token budget")
                    return
                key = sessions.authorize(self.headers.get("x-api-key", ""), payload.get("model"))
                if key is None:
                    self.reply(403, "session rejected")
                    return
                request = Request(
                    UPSTREAM + "/v1/messages",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": key,
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
                    print(json.dumps({"gateway_status": upstream.status}), flush=True)
                    self.send_response(upstream.status)
                    self.send_header(
                        "Content-Type", upstream.headers.get("Content-Type", "application/json")
                    )
                    self.end_headers()
                    while chunk := upstream.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
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
