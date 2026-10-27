# Linux 部署与恢复

本文提供 Ubuntu/systemd 的 Hub API、Web Console 与单并发 Worker Scheduler 安装模板。它不替代真实 Harbor/E2B/模型端到端验收；部署服务默认仍关闭任务写入和执行，只有完成凭证配置、网络与存储验收后才能由操作者显式打开。

## 组件和边界

- `rollforge-hub.service` 只监听 `127.0.0.1:8000`。`rollforge-web.service` 只监听 loopback，端口由受保护的 `web.env` 配置，仅适合本机或 SSH 隧道访问；当前 Web 使用单一服务端 USER Token，没有多用户登录，不得接公网反向代理。
- `rollforge-scheduler.service` 以并发 1 运行，适合目前 8 GiB 的实验主机。增加并发或第二个 Scheduler 前，先做容量与 Harbor/E2B 竞争验收。
- PostgreSQL 是任务和租约的权威状态源；Redis 只用于通知/缓存。对象存储需由 Hub 与 Worker 共同访问。Compose 使用固定 digest 的 PGSTY Silo（MinIO 社区版兼容分支）提供 S3 API；Silo 保留 `MINIO_*` 配置与 MinIO 存储格式，项目的环境变量和端口名仍沿用 MinIO 兼容命名。Silo 是独立社区维护项目，不隶属于 MinIO；升级前需核对其兼容性和安全公告。
- RollForge Compose 使用 loopback 主机端口 55432、56379、59000、59001，分别映射 PostgreSQL、Redis、MinIO API 和 Console；端口可通过 `ROLLFORGE_*_HOST_PORT` 覆盖。预检会拒绝 E2B 常用的 5432、6379、9000、9001，以及实际已占用的任何配置端口，并校验容器内目标端口和命名卷。不要复用 E2B 的容器、数据库卷或默认监听端口。
- Worker Runtime 会话单独放在 `/var/lib/rollforge/private/runtime.env`，不能把 Hub token、对象存储密钥或上游 `ANTHROPIC_API_KEY` 放入其中。

systemd 模板的基础验证见 [Linux systemd 部署模板验证](validation/2026-10-26-linux-deployment.md)；本次独立端口、Web unit 与部署预检器验证见 [部署预检记录](validation/2026-10-26-deployment-preflight.md)。

## 安装

以下命令需要在目标 Linux 主机以管理员权限执行。先准备已审核的代码目录与固定依赖，再安装服务；不要把真实 `.env` 文件放进 Git checkout。

首次部署前先确认权限：`sudo -n true` 成功才表示自动化会话可执行非交互式管理员操作。目标节点已完成账号、配置目录、独立数据服务和 Hub/Web systemd 安装；截至 2026-10-27，当前会话的非交互式 sudo 可用，Hub/Web 正常运行。下列命令供新节点或重装使用；目标节点现状及未通过关卡见[部署验收记录](validation/2026-10-26-deployment-audit.md)。不要把 Docker socket/组权限当作 sudo 替代，也不要用它绕过系统服务与凭证权限边界。仓库目前只验收 systemd 系统级部署模板，尚无 rootless Docker 部署验收路径。

```sh
sudo useradd --system --home-dir /var/lib/rollforge --create-home --shell /usr/sbin/nologin rollforge
sudo install -d -o root -g rollforge -m 0750 /etc/rollforge
sudo install -d -o rollforge -g rollforge -m 0700 /var/lib/rollforge
sudo install -d -o rollforge -g rollforge -m 0700 /var/lib/rollforge/private /var/lib/rollforge/spool
sudo install -d -o root -g root -m 0755 /opt/rollforge
```

先准备系统级构建工具。unit 使用 `ProtectHome=true`，因此 Node.js、npm 和 uv 不能只安装在 `tyu` 的 nvm 或 `~/.local/bin` 中：

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git

# Node.js 22 系统级安装，为生产构建和 systemd 服务提供 /usr/bin/node、npm。
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh
sudo -E bash /tmp/nodesource_setup.sh
sudo apt-get install -y nodejs
rm -f /tmp/nodesource_setup.sh

# uv 安装到系统 PATH，避免 sudo secure_path 找不到用户目录中的 uv。
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh /tmp/uv-install.sh
rm -f /tmp/uv-install.sh

/usr/bin/node --version
/usr/bin/npm --version
/usr/local/bin/uv --version
```

前面的目录准备命令会在 `/opt/rollforge` 下创建空的 Web cache 占位目录。先只删除这些空目录，再把 GitHub 主分支检出到该路径；若 `rmdir` 失败，表示目录内已有文件，应先检查内容，不要强行删除：

```sh
set -e
sudo rmdir \
  /opt/rollforge/apps/hub-web/.next/cache \
  /opt/rollforge/apps/hub-web/.next \
  /opt/rollforge/apps/hub-web \
  /opt/rollforge/apps \
  /opt/rollforge
sudo git clone --branch main --single-branch \
  https://github.com/iamaaronyu/RollForge.git /opt/rollforge
```

确认 checkout 后，使用 system-wide uv，并将 uv 管理的 Python 固定在 `ProtectHome=true` 可读取的 `/opt/rollforge-python`：

```sh
sudo install -d -o root -g root -m 0755 /opt/rollforge-python
cd /opt/rollforge
sudo env UV_PYTHON_INSTALL_DIR=/opt/rollforge-python \
  /usr/local/bin/uv sync --all-packages --locked --python 3.12
sudo env UV_PYTHON_INSTALL_DIR=/opt/rollforge-python \
  /usr/local/bin/uv sync --project integration/harbor-runtime --python 3.12 --locked
sudo npm --prefix apps/hub-web ci
sudo npm --prefix apps/hub-web run build
sudo chown -R root:root /opt/rollforge
sudo chmod -R go-w /opt/rollforge
sudo install -d -o rollforge -g rollforge -m 0750 /opt/rollforge/apps/hub-web/.next/cache
```

Next.js 生产服务要求系统级 Node.js 20.9 或更新版本；以上安装 Node.js 22，路径应为 `/usr/bin/node`。不要依赖登录用户的 nvm 路径，因为 unit 使用 `ProtectHome=true`。uv Runtime venv 使用的 Python 位于 `/opt/rollforge-python`，不可放在 `/root/.local/share/uv`，否则服务的 `ProtectHome=true` 会阻止访问。

## 独立数据服务与部署预检

在 `/etc/rollforge/infra.env` 创建 0600 文件。PostgreSQL 与 MinIO 口令必须是不同的随机值，不能沿用 `.env.example` 的开发口令。Compose 不提供数据库或 MinIO 的口令默认值；`make infra` 不再读取仓库 `.env`，必须显式传入 `ROLLFORGE_INFRA_ENV`。启动器会检查文件权限、强凭证、隔离端口、容器端口映射、端口冲突和持久卷，并清除 shell 同名变量，确保只使用该受保护文件：

```dotenv
POSTGRES_USER=rollforge
POSTGRES_PASSWORD=<独立随机强口令>
POSTGRES_DB=rollforge
MINIO_ROOT_USER=rollforge-storage
MINIO_ROOT_PASSWORD=<另一组独立随机强口令>
ROLLFORGE_POSTGRES_HOST_PORT=55432
ROLLFORGE_REDIS_HOST_PORT=56379
ROLLFORGE_MINIO_HOST_PORT=59000
ROLLFORGE_MINIO_CONSOLE_HOST_PORT=59001
```

在填好受保护的 `hub.env`、`worker.env` 后，再运行完整只读预检。它检查配置文件权限、强制关闭首次部署写入和执行开关、校验 USER/WORKER Token 摘要匹配、Hub 数据库与对象存储地址确实匹配本部署的 loopback 独立端口、Compose loopback 绑定、端口冲突和 PostgreSQL/MinIO 命名卷；若提供 `web.env`，也校验 Node.js、loopback Hub 地址和 USER Token 归属。预检错误不打印配置值：

```sh
sudo /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/check_deployment.py \
  --compose-env /etc/rollforge/infra.env \
  --hub-env /etc/rollforge/hub.env \
  --worker-env /etc/rollforge/worker.env \
  --web-env /etc/rollforge/web.env
```

预检须在第一次启动 RollForge 数据服务和 Hub/Web 前运行；它通过尝试绑定 loopback 端口检查当前冲突，不会启动容器或服务，也不证明依赖健康。完整预检通过后，启动器会再次检查独立服务配置并启动 Compose 项目；随后用 `docker compose ps` 核对容器状态：

```sh
sudo env ROLLFORGE_INFRA_ENV=/etc/rollforge/infra.env make -C /opt/rollforge infra
sudo docker compose --project-name rollforge \
  --env-file /etc/rollforge/infra.env \
  --file /opt/rollforge/infra/docker-compose/compose.yaml ps
```

PostgreSQL 与 MinIO 数据保存在独立命名卷 `rollforge_postgres-data` 和 `rollforge_minio-data`。Redis 是可重建缓存。禁止用 `down -v` 清理；上线前需另行设置加密备份与读回演练。

在 `/etc/rollforge/hub.env` 和 `/etc/rollforge/worker.env` 创建本机专用配置，权限设为 `root:rollforge 0640`。不要通过命令行参数、shell history、工单或聊天传递 token。环境文件格式为 `KEY=value`；值中不要使用 shell 展开语法。

Hub 至少需要：`ROLLFORGE_ENVIRONMENT=production`、`ROLLFORGE_DATABASE_URL`、`ROLLFORGE_API_CREDENTIALS` 和对象存储 endpoint/bucket/access key/secret key。`ROLLFORGE_GLOBAL_EXECUTION_LIMIT` 是所有 Hub 进程共享的数据库领取上限，范围为 1–256，默认 1；同一 PostgreSQL 集群的 Hub 实例必须配置相同值。领取事务通过 PostgreSQL 锁定容量决策；过期但尚未回收的 Execution 继续占位，避免恢复期间超额启动。Scheduler 的 `ROLLFORGE_SCHEDULER_CONCURRENCY` 仍是单进程上限，其值可以高于全局上限，但不会突破 Hub 的数据库上限。

单机部署的数据库 URL 必须指向 `127.0.0.1:<ROLLFORGE_POSTGRES_HOST_PORT>/<POSTGRES_DB>`，MinIO endpoint 必须是 `http://127.0.0.1:<ROLLFORGE_MINIO_HOST_PORT>`。如配置 Redis，也必须指向 `127.0.0.1:<ROLLFORGE_REDIS_HOST_PORT>`。完整预检会核对这些地址与 Compose 端口，防止误连 E2B 或其他服务。USER 与 WORKER 使用不同随机 Token；API credentials 只保存其 SHA256 摘要，原始 Token 由对应客户端保管。Hub 与 Worker 使用相同对象存储 endpoint/bucket、不同最小权限访问密钥。若 MinIO 仅在同一主机 loopback 提供 HTTP，Hub 和 Worker 均须显式设置 `ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP=true`；不得用该开关接受 LAN HTTP。先保持 `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED=false`。

Worker 配置至少需要：`ROLLFORGE_WORKER_HUB_URL`、`ROLLFORGE_WORKER_HUB_TOKEN`、`ROLLFORGE_WORKER_RUNTIME_PYTHON=/opt/rollforge/integration/harbor-runtime/.venv/bin/python`、`ROLLFORGE_WORKER_RUNTIME_ENV_FILE=/var/lib/rollforge/private/runtime.env`、经过验收的 `ROLLFORGE_WORKER_GATEWAY_URL`、与 Hub 相同的对象存储 endpoint/bucket、独立对象存储 access key/secret key，以及 spool 路径 `/var/lib/rollforge/spool`。网关地址默认要求 HTTPS；仅可信 LAN 验收允许显式设置 `ROLLFORGE_WORKER_ALLOW_LAN_HTTP=true`。先设置 `ROLLFORGE_WORKER_RUNS_ENABLED=false`、`ROLLFORGE_SCHEDULER_ENABLED=false`、`ROLLFORGE_SCHEDULER_CONCURRENCY=1`。

Runtime 会话目录和文件使用独立权限。目录由 `rollforge` 拥有且为 0700，文件由 `rollforge` 拥有且为 0600，以满足 Worker 检查并允许服务读取：

```sh
sudo chown root:rollforge /etc/rollforge/hub.env /etc/rollforge/worker.env
sudo chmod 0640 /etc/rollforge/hub.env /etc/rollforge/worker.env
sudo install -d -o rollforge -g rollforge -m 0700 /var/lib/rollforge/private
sudo install -o rollforge -g rollforge -m 0600 /dev/null /var/lib/rollforge/private/runtime.env
```

`runtime.env` 只含短时受限 `ROLLFORGE_MODEL_SESSION_TOKEN`、`E2B_API_KEY` 和必要的 E2B endpoint 配置。直接 DeepSeek API key 不得放入 Worker 或 Runtime 配置。

模型会话 Token 在可信 Mac 上由本地网关管理 CLI 签发，不是从 DeepSeek 控制台获取。确认 E2B 节点网络策略已获授权后，可在 Mac 的终端中生成短时、限次、限费用 Token，并通过 SSH 标准输入写入 Linux 私密环境文件。以下流程不回显 Token；先要求服务器环境文件权限正确、已有 E2B Key 且没有旧 Token。示例限制为 10 分钟、最多 15 个模型请求、总预算 0.05 美元；按本次验收需要再调低，不要扩大默认额度。

```sh
set -euo pipefail
umask 077
token_file="$(mktemp "$HOME/.rollforge/private/session.XXXXXX")"
installed=0
cleanup() {
  if [ "$installed" -eq 0 ]; then
    if [ -s "$token_file" ]; then
      uv run python scripts/manage_gateway_sessions.py revoke \
        --config "$HOME/.rollforge/private/gateway.json" \
        < "$token_file" >/dev/null 2>&1 || true
    fi
    rm -f "$token_file"
  fi
}
trap cleanup EXIT

ssh -o BatchMode=yes tyu@100.106.244.122 \
  'sudo -n test "$(sudo -n stat -c "%U:%G %a" /var/lib/rollforge/private/runtime.env)" = "rollforge:rollforge 600" && sudo -n grep -q "^E2B_API_KEY=." /var/lib/rollforge/private/runtime.env && ! sudo -n grep -q "^ROLLFORGE_MODEL_SESSION_TOKEN=" /var/lib/rollforge/private/runtime.env'
uv run python scripts/manage_gateway_sessions.py issue \
  --config "$HOME/.rollforge/private/gateway.json" \
  --ttl-seconds 600 --max-requests 15 --max-cost-usd 0.05 \
  > "$token_file"
{
  printf '\nROLLFORGE_MODEL_SESSION_TOKEN='
  cat "$token_file"
  printf '\n'
} | ssh -o BatchMode=yes tyu@100.106.244.122 \
  'sudo -n tee -a /var/lib/rollforge/private/runtime.env >/dev/null'
installed=1
```

验收结束后，在 Mac 继续使用本地 token 文件撤销会话，再用 `sudoedit /var/lib/rollforge/private/runtime.env` 删除对应 Token 行；不要删除 E2B Key。最后执行 `rm -f "$token_file"` 并运行 `trap - EXIT`。如果传输失败，Shell trap 会撤销刚签发的 Token；检查并移除可能写入了一半的环境变量行后再重试。Token 到期也会由网关拒绝，但不替代主动撤销和清理。

复制服务模板并先做静态检查：

```sh
sudo install -m 0644 infra/systemd/rollforge-hub.service /etc/systemd/system/
sudo install -m 0644 infra/systemd/rollforge-web.service /etc/systemd/system/
sudo install -m 0644 infra/systemd/rollforge-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/rollforge-hub.service /etc/systemd/system/rollforge-web.service /etc/systemd/system/rollforge-scheduler.service
sudo -u rollforge /opt/rollforge/.venv/bin/python -m rollforge_scheduler --check
```

首次迁移通过 transient systemd service 读取受保护的 Hub 环境文件，不把数据库 URL 放到命令行：

```sh
sudo systemd-run --wait --pipe --collect \
  --uid=rollforge --gid=rollforge \
  --property=WorkingDirectory=/opt/rollforge/apps/hub-api \
  --property=EnvironmentFile=/etc/rollforge/hub.env \
  /opt/rollforge/.venv/bin/alembic -c /opt/rollforge/apps/hub-api/alembic.ini upgrade head
```

然后只启动 Hub 和本机 Web；不要启用 Scheduler：

```sh
sudo systemctl enable --now rollforge-hub.service
sudo systemctl enable --now rollforge-web.service
sudo systemctl status rollforge-hub.service rollforge-web.service
curl --fail http://127.0.0.1:8000/health/ready
```

在 `web.env` 设置空闲的 `ROLLFORGE_WEB_HOST_PORT`（目标节点的 3000 已占用时可先尝试 3300）；预检会检查该端口。Hub 和 Web unit 均只绑定 loopback。需要从管理电脑访问 Web 与 Hub API 时建立 SSH 隧道：

```sh
LOCAL_WEB_PORT=3000
REMOTE_WEB_PORT=3300
LOCAL_HUB_PORT=18000
SSH_TARGET=user@host
ssh -N \
  -L "${LOCAL_WEB_PORT}:127.0.0.1:${REMOTE_WEB_PORT}" \
  -L "${LOCAL_HUB_PORT}:127.0.0.1:8000" \
  "$SSH_TARGET"
```

Web 通过服务器本机 `127.0.0.1:8000` 访问 Hub；本地 API 调试则访问 `http://127.0.0.1:18000`。隧道的本地端口若已占用，应调整左侧端口，不改变远端服务的 loopback 绑定。

`web.env` 只含 `ROLLFORGE_API_URL=http://127.0.0.1:8000`、匹配 Hub USER 角色的 `ROLLFORGE_WEB_USER_TOKEN`、`ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS=false` 和 `ROLLFORGE_WEB_HOST_PORT`；禁止 `NEXT_PUBLIC_` 凭证。Web 仅用于单用户本地验收。Hub 的 `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED`、Worker 的 `ROLLFORGE_WORKER_RUNS_ENABLED`、Scheduler 的 `ROLLFORGE_SCHEDULER_ENABLED` 都保持 `false`。Scheduler unit 只安装，不启用。

Scheduler 在 `ROLLFORGE_SCHEDULER_ENABLED` 或 `ROLLFORGE_WORKER_RUNS_ENABLED` 任一关闭时会拒绝启动，因此准备阶段只运行上面的无执行 `--check`，不要启用 Scheduler unit。完成真实数据库、对象存储、Hub 鉴权、Harbor/E2B、受限模型会话与恢复验收后，再由操作者在 `worker.env` 同时打开两个开关，并启动 Scheduler：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now rollforge-scheduler.service
sudo systemctl status rollforge-hub.service rollforge-scheduler.service
sudo journalctl -u rollforge-hub.service -u rollforge-scheduler.service --since today
```

开放单 Trial 执行前，必须另行通过真实 PostgreSQL、对象存储、Hub 鉴权、Harbor/E2B 与受限模型网关验收，并确认重启恢复演练。之后由操作者在 root 管理的 `hub.env` 打开写入门，在 `worker.env` 同时打开 Worker 和 Scheduler 开关；本仓库模板本身不启用这些开关。遵循 [Worker 验证记录](validation/2026-10-06-s1-worker.md) 与 [多 Trial 验收手册](multitrial-acceptance.md)。

正式开门前逐项留存可复核证据：数据库迁移和真实租约事务测试；对象存储 Hub/Worker 权限分离、上传/读取/摘要校验和重启后读回；USER/WORKER Token 鉴权、轮换与凭证扫描；受限网关真实模型调用、无效/过期会话拒绝及预算账本重启保留；`native-ready.json`、`pending-commit.json` 的进程/主机恢复、租约回收与备份读回。任一项未通过，三个开关继续为 false。目标节点已有数据库/存储/API 控制面验收，但真实平台 Worker 多 Trial、网关常驻访问、加密备份恢复和宿主故障演练仍未完成；当前四个写入/执行门全关。

## 升级与恢复

PostgreSQL、对象桶与网关请求预算的停写备份、隔离恢复步骤和恢复边界见 [跨组件备份与恢复手册](backup-recovery.md)。该工具只恢复到空 DB/隔离 bucket；不原地覆盖，也不改变正式执行开关。

升级前停止 Scheduler 以停止领取并等待当前执行优雅退出，然后停止 Hub/Web；记录当前提交并确认服务 checkout 没有本地改动。目标提交应先在隔离 checkout 完成 `make check`、`make runtime-check`、锁文件和迁移差异检查。确认数据库迁移策略和备份后，再把服务 checkout 快进到固定提交、重新安装锁定依赖并重建 Web，运行 `alembic upgrade head` 后启动 Hub，验证 `/health/ready`，再启动 Web 并验证首页。服务路径固定在 `/opt/rollforge` 时，切换代码后必须重新安装 Python console scripts（例如 `uv sync --reinstall`），防止脚本 shebang 仍指向临时构建目录。Web 与 Hub readiness 应采用有界重试，不要在 `systemctl start` 返回后立即假定端口已监听。

若切换后任一验收失败，停止新服务，把干净 checkout 切回记录的旧提交，按原锁文件重新安装依赖并重建 Web，再启动旧 Hub/Web 验证 readiness。迁移已改变数据库时，不能只回切代码；必须执行预先审核的向前兼容或恢复方案。不要直接删除旧版本回退点，也不要在有活动执行、未完成租约或未知数据库写入时覆盖代码。2026-10-27 的实际升级与恢复记录见[Linux 升级验收](validation/2026-10-27-linux-upgrade.md)。

主机重启后依次检查 PostgreSQL、对象存储、Hub readiness 和 Scheduler 日志。未完成执行由 PostgreSQL 租约过期回收；若本地 spool 中存在 `native-ready.json` 或 `pending-commit.json`，使用 Worker 的 `--resume` 恢复对应 Execution，不重跑模型。确认 Worker 已接受提交或明确标记失败后，才清理对应 spool。不要清除数据库记录来“修复”孤儿产物；对象存储清理需按 Execution manifest 核对。

日志可能包含任务错误上下文，应限制 journald 读取权限并配置保留期；禁止开启可能输出环境变量、请求 Authorization header 或原始模型会话的调试日志。
