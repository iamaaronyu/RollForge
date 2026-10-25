# Linux 部署与恢复

本文提供 Ubuntu/systemd 的 Hub API 与单并发 Worker Scheduler 安装模板。它不替代真实 Harbor/E2B/模型端到端验收；部署服务默认仍关闭任务写入和执行，只有完成凭证配置、网络与存储验收后才能由操作者显式打开。

## 组件和边界

- `rollforge-hub.service` 只监听 `127.0.0.1:8000`，应由已有 TLS 反向代理转发；不要把 Uvicorn 端口直接暴露到公网。
- `rollforge-scheduler.service` 以并发 1 运行，适合目前 8 GiB 的实验主机。增加并发或第二个 Scheduler 前，先做容量与 Harbor/E2B 竞争验收。
- PostgreSQL 是任务和租约的权威状态源；Redis 只用于通知/缓存。对象存储需由 Hub 与 Worker 共同访问。
- Worker Runtime 会话单独放在 `/var/lib/rollforge/private/runtime.env`，不能把 Hub token、对象存储密钥或上游 `ANTHROPIC_API_KEY` 放入其中。

本次模板状态与验证范围见 [Linux systemd 部署模板验证](validation/2026-10-26-linux-deployment.md)。

## 安装

以下命令需要在目标 Linux 主机以管理员权限执行。先准备已审核的代码目录与固定依赖，再安装服务；不要把真实 `.env` 文件放进 Git checkout。

```sh
sudo useradd --system --home-dir /var/lib/rollforge --create-home --shell /usr/sbin/nologin rollforge
sudo install -d -o root -g rollforge -m 0750 /etc/rollforge
sudo install -d -o rollforge -g rollforge -m 0700 /var/lib/rollforge
sudo install -d -o root -g root -m 0755 /opt/rollforge
```

将审查过的仓库检出到 `/opt/rollforge`，并确认目录对 `rollforge` 用户可读、不可写；随后用该版本锁文件安装依赖：

```sh
cd /opt/rollforge
sudo uv sync --all-packages --locked
sudo uv sync --project integration/harbor-runtime --python 3.12 --locked
sudo chown -R root:root /opt/rollforge
sudo chmod -R go-w /opt/rollforge
```

在 `/etc/rollforge/hub.env` 和 `/etc/rollforge/worker.env` 创建本机专用配置，权限设为 `root:rollforge 0640`。不要通过命令行参数、shell history、工单或聊天传递 token。环境文件格式为 `KEY=value`；值中不要使用 shell 展开语法。

Hub 至少需要：`ROLLFORGE_ENVIRONMENT=production`、`ROLLFORGE_DATABASE_URL`、`ROLLFORGE_API_CREDENTIALS`。先保持 `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED=false`。API credentials 配置只保存 USER/WORKER token 的 SHA256 摘要，原始 token 由客户端保管。

Worker 配置至少需要：`ROLLFORGE_WORKER_HUB_URL`、`ROLLFORGE_WORKER_HUB_TOKEN`、`ROLLFORGE_WORKER_RUNTIME_PYTHON=/opt/rollforge/integration/harbor-runtime/.venv/bin/python`、`ROLLFORGE_WORKER_RUNTIME_ENV_FILE=/var/lib/rollforge/private/runtime.env`、经过验收的 `ROLLFORGE_WORKER_GATEWAY_URL`、对象存储 endpoint/bucket/access key/secret key，以及 spool 路径 `/var/lib/rollforge/spool`。先设置 `ROLLFORGE_WORKER_RUNS_ENABLED=false`、`ROLLFORGE_SCHEDULER_ENABLED=false`、`ROLLFORGE_SCHEDULER_CONCURRENCY=1`。

Runtime 会话目录和文件使用独立权限。目录由 `rollforge` 拥有且为 0700，文件由 `rollforge` 拥有且为 0600，以满足 Worker 检查并允许服务读取：

```sh
sudo chown root:rollforge /etc/rollforge/hub.env /etc/rollforge/worker.env
sudo chmod 0640 /etc/rollforge/hub.env /etc/rollforge/worker.env
sudo install -d -o rollforge -g rollforge -m 0700 /var/lib/rollforge/private
sudo install -o rollforge -g rollforge -m 0600 /dev/null /var/lib/rollforge/private/runtime.env
```

`runtime.env` 只含短时受限 `ROLLFORGE_MODEL_SESSION_TOKEN`、`E2B_API_KEY` 和必要的 E2B endpoint 配置。直接 DeepSeek API key 不得放入 Worker 或 Runtime 配置。

复制服务模板并先做静态检查：

```sh
sudo install -m 0644 infra/systemd/rollforge-hub.service /etc/systemd/system/
sudo install -m 0644 infra/systemd/rollforge-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/rollforge-hub.service /etc/systemd/system/rollforge-scheduler.service
sudo systemctl enable --now rollforge-hub.service
```

Hub 元数据 API 验收通过后，Scheduler 可先以双重关闭配置启动，检查状态和日志不会领取任务：

```sh
sudo systemctl enable --now rollforge-scheduler.service
sudo systemctl status rollforge-hub.service rollforge-scheduler.service
sudo journalctl -u rollforge-hub.service -u rollforge-scheduler.service --since today
```

开放单 Trial 执行前，必须另行通过真实 PostgreSQL、对象存储、Hub 鉴权、Harbor/E2B 与受限模型网关验收，并确认重启恢复演练。之后由操作者在 root 管理的 `hub.env` 打开写入门，在 `worker.env` 同时打开 Worker 和 Scheduler 开关；本仓库模板本身不启用这些开关。遵循 [Worker 验证记录](validation/2026-10-06-s1-worker.md) 与 [多 Trial 验收手册](multitrial-acceptance.md)。

## 升级与恢复

升级前停止 Scheduler 以停止领取并等待当前执行优雅退出，然后停止 Hub；保存数据库备份并记录当前代码版本。部署新代码和锁文件后运行 `make check`、`make runtime-check`、数据库迁移检查，再启动 Hub，最后启动 Scheduler。不要在活动执行期间覆盖 Runtime 或任务目录。

主机重启后依次检查 PostgreSQL、对象存储、Hub readiness 和 Scheduler 日志。未完成执行由 PostgreSQL 租约过期回收；若本地 spool 中存在 `native-ready.json` 或 `pending-commit.json`，使用 Worker 的 `--resume` 恢复对应 Execution，不重跑模型。确认 Worker 已接受提交或明确标记失败后，才清理对应 spool。不要清除数据库记录来“修复”孤儿产物；对象存储清理需按 Execution manifest 核对。

日志可能包含任务错误上下文，应限制 journald 读取权限并配置保留期；禁止开启可能输出环境变量、请求 Authorization header 或原始模型会话的调试日志。
