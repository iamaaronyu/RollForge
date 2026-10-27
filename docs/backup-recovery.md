# RollForge 跨组件备份与恢复

本工具为 PostgreSQL、S3 兼容对象桶和模型网关 SQLite 请求账本生成同一恢复包。只有在 Hub 写入、Worker、Scheduler 和模型网关都停止后才能创建备份；PostgreSQL dump、对象列表/读取和 SQLite 快照不是跨系统原子事务，停止所有写入是它们构成同一恢复点的前提。

备份包及对象内容是明文，包含任务快照、执行结果和产物。备份目标必须位于加密卷或加密文件系统；目录权限 0700、文件权限 0600 只提供本机访问隔离，不等于静态加密。不要把备份包、网关配置、`.env` 或临时恢复目标提交到 Git。

## 创建和检查

安装版本匹配的 `pg_dump`、`pg_restore`、`psql`，或使用下述数据库容器客户端方式；创建仅 `rollforge` 可读写的加密备份目录。由管理员创建 `/etc/rollforge/backup.env`（`root:rollforge 0640`），其中包含 `ROLLFORGE_DATABASE_URL` 和 `ROLLFORGE_BACKUP_OBJECT_STORAGE_ENDPOINT`、`_BUCKET`、`_ACCESS_KEY`、`_SECRET_KEY`（变量名前缀均为 `ROLLFORGE_BACKUP_OBJECT_STORAGE`）。备份对象存储身份必须独立于 Hub/Worker：仅授予指定 bucket 的 `ListBucket` 与 `GetObject`，不授予写入、删除或其他 bucket 权限；Hub 的最小权限角色保持不变。不得将密钥写入命令行、shell history 或日志。网关账本路径取决于启动 `scripts/model_gateway.py --config ...` 时所用配置路径，预算数据库是该路径替换扩展名为 `.sqlite3` 后的文件。备份不复制含上游 Key 的网关 JSON 配置。

当前模型网关运行在 Mac，数据库与对象存储在 Linux。创建跨主机备份前，先停止 Mac 网关，防止 SQLite 状态在复制期间变化；通过 SSH 把 Mac 的预算账本临时复制到 Linux 私有 spool 目录，备份命令完成后删除该暂存文件，再按原 LaunchAgent 配置启动 Mac 网关。只传 `gateway.sqlite3`，不要传输或复制 `gateway.json`。示例中的 Mac 路径和 Linux 账号按当前部署填写；传输失败时不要继续创建备份。

```sh
# Mac：确保会话已撤销且无人使用网关后停止 LaunchAgent，再传输纯预算账本。
set -e
launchctl bootout "gui/$(id -u)/com.rollforge.model-gateway"
scp -p "$HOME/.rollforge/private/gateway.sqlite3" tyu@100.106.244.122:gateway-budget.sqlite3
ssh tyu@100.106.244.122 \
  'sudo install -o rollforge -g rollforge -m 0600 /home/tyu/gateway-budget.sqlite3 /var/lib/rollforge/spool/gateway-budget.sqlite3 && rm -f /home/tyu/gateway-budget.sqlite3'
```

如果传输失败，不要继续备份；先清理不完整的 Linux 暂存文件并恢复网关服务。仅在备份成功并完成校验后，才删除 spool 副本并启动 Mac 网关：

若宿主机没有 PostgreSQL 客户端，但运行中的 PostgreSQL 容器内含匹配版本客户端，可在 `/etc/rollforge/backup.env` 中增加 `ROLLFORGE_POSTGRES_CLIENT_CONTAINER=rollforge-postgres-1`。工具通过 Docker 本地 socket 在该容器中运行固定的 PostgreSQL 客户端，并将 dump 流写入加密卷；数据库连接密码不会传入 Docker 命令或容器环境。Docker socket 仅 root 可访问，因此这种模式的创建和恢复命令必须由管理员以 root 启动。不要把 `rollforge` 加入 `docker` 组。管理员创建的备份包需在校验前执行 `sudo chown -R rollforge:rollforge "$BACKUP_PATH"`，之后以 `rollforge` 身份校验。

停写并停止服务后，由管理员通过受控终端运行：

```sh
BACKUP_ID=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_PATH="/mnt/rollforge-backups/${BACKUP_ID}"
sudo systemd-run --wait --pipe --collect \
  --property=WorkingDirectory=/opt/rollforge \
  --property=EnvironmentFile=/etc/rollforge/backup.env \
  /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/backup_rollforge.py create \
    --destination "$BACKUP_PATH" \
    --gateway-budget-db /var/lib/rollforge/spool/gateway-budget.sqlite3 \
    --acknowledge-quiescent
```

上例以管理员身份运行，适用于容器客户端模式。若使用宿主机 PostgreSQL 客户端，可在 `systemd-run` 参数中加入 `--uid=rollforge --gid=rollforge`，使备份进程直接以服务账号运行。容器客户端模式下，命令成功后执行 `sudo chown -R rollforge:rollforge "$BACKUP_PATH"`，再按前文以 `rollforge` 身份运行 `verify`。

`/mnt/rollforge-backups` 必须是已挂载的加密卷，且父目录归 `rollforge` 所有、权限 0700。预检会拒绝非私有目录、活动写入未确认的调用、缺失/损坏的 SQLite 账本和不安全对象存储 URL。创建成功后，用同一账号完整校验：

```sh
sudo -u rollforge /opt/rollforge/.venv/bin/python \
  /opt/rollforge/scripts/backup_rollforge.py verify \
  --bundle "/mnt/rollforge-backups/${BACKUP_ID}"
```

```sh
ssh tyu@100.106.244.122 \
  'sudo rm -f /var/lib/rollforge/spool/gateway-budget.sqlite3'
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.rollforge.model-gateway.plist"
```

包内 `manifest.json` 保存 PostgreSQL 逻辑 dump、每个对象的 key/字节数/SHA-256、SQLite 账本和全部本地文件摘要。数据库指纹覆盖 Job、Trial、Execution、Asset、Revision、迁移版本的逐行规范化摘要，以及 Trial/Execution 状态汇总。创建时会确认数据库引用的 Task/Revision 对象存在，并逐一确认已提交 Manifest 对象内容与 PostgreSQL 中的 `manifest_digest` 相符。

## 恢复到隔离目标

先在隔离环境创建一个新的空 PostgreSQL 数据库和目标 S3 bucket。恢复目标连接必须用独立文件 `/etc/rollforge/restore-target.env` 提供：`ROLLFORGE_RESTORE_DATABASE_URL`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_ENDPOINT`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_BUCKET`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_ACCESS_KEY`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_SECRET_KEY`；loopback MinIO HTTP 测试才可另设 `ROLLFORGE_RESTORE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP=true`。该文件建议 `root:rollforge 0640`。使用容器客户端时，也需在此文件设置 `ROLLFORGE_POSTGRES_CLIENT_CONTAINER=rollforge-postgres-1`。目标数据库名必须与源库不同；不要把源环境文件用于恢复。

目标 bucket 应为空。若上次恢复中途失败，工具允许其中已有对象为备份中完全相同的一部分，便于重试；多余或同 key 不同内容会拒绝。恢复先逐项上传并读回校验对象，再把 PostgreSQL 自定义归档通过单事务导入空库，最后复算数据库行指纹、状态分布、Revision 摘要及对象引用指纹。工具不会 `DROP`、清空或原地覆盖已有数据库/Bucket。由管理员启动：

```sh
sudo systemd-run --wait --pipe --collect \
  --property=WorkingDirectory=/opt/rollforge \
  --property=EnvironmentFile=/etc/rollforge/restore-target.env \
  /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/backup_rollforge.py restore \
    --bundle "$BACKUP_PATH" \
    --acknowledge-isolated-targets
```

容器客户端模式下，恢复命令也必须由管理员以 root 启动，以访问 Docker socket；宿主机客户端模式可按需加入 `--uid=rollforge --gid=rollforge`。恢复目标仍须是独立的空数据库和空 bucket。

恢复源与目标配置若同时存在，工具会拒绝源 PostgreSQL 同名数据库或完全相同的对象 endpoint/bucket。恢复后应在隔离环境核对数据库 fingerprint、对象数量与摘要、Hub 只读查询和就绪检查；确认后再由管理员更新应用环境文件指向恢复目标。PostgreSQL 全局角色/所有权/ACL、PostgreSQL 服务配置、S3 bucket 策略/用户、对象版本历史与删除标记均不在此逻辑备份范围内，需分别按部署配置恢复。恢复目标应预先设置正确的身份权限和加密策略。

## 网关请求预算

网关运行于 Mac 时，先按“创建和检查”中的步骤把 Mac 账本传到 Linux 的 spool；该步骤会先停止 LaunchAgent。以 spool 账本为 `restore-budget` 的现存账本，工具将备份与现存记录按 `token_sha256` 合并，计数取两者最大值；现存记录保留，不能通过旧备份把已用请求数减小。即使快照中的计数小于当前账本，该 token 仍保留当前较大计数；快照缺少的较新 token 记录也不会被删除。若现存账本有 SQLite WAL/SHM/journal sidecar 或结构损坏，工具会拒绝替换，需先调查数据库状态。

若原账本已丢失，旧快照无法知道快照之后已经消费的请求额度。新主机恢复要求网关配置权限正确且 `sessions` 为空；工具拒绝活动会话，也不会自动签发新会话。恢复后保持网关无会话，待模型账户侧预算和快照之后的调用量核对完成，再由授权操作者决定后续会话及额度；不能把旧配置/旧会话恢复回来，也不能在未知用量时重置请求预算。

```sh
sudo -u rollforge /opt/rollforge/.venv/bin/python \
  /opt/rollforge/scripts/backup_rollforge.py restore-budget \
  --bundle "$BACKUP_PATH" \
  --gateway-budget-db /var/lib/rollforge/spool/gateway-budget.sqlite3 \
  --acknowledge-gateway-stopped

# Linux：将合并后的账本暂存到 tyu 私有主目录，权限保持 0600。
sudo install -o tyu -g tyu -m 0600 \
  /var/lib/rollforge/spool/gateway-budget.sqlite3 \
  /home/tyu/gateway-budget-restored.sqlite3
sudo rm -f /var/lib/rollforge/spool/gateway-budget.sqlite3

# Mac：原子替换本机账本后删除传输副本，再启动 LaunchAgent。
scp -p tyu@100.106.244.122:gateway-budget-restored.sqlite3 \
  "$HOME/.rollforge/private/gateway.sqlite3.restore"
mv "$HOME/.rollforge/private/gateway.sqlite3.restore" \
  "$HOME/.rollforge/private/gateway.sqlite3"
ssh tyu@100.106.244.122 'rm -f /home/tyu/gateway-budget-restored.sqlite3'
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.rollforge.model-gateway.plist"
```

若目标 Mac 尚无账本，先使用目标配置启动一次新版本网关以创建空账本，确认配置中的 `sessions` 为空后停止网关，再按上述流程合并和回传。不要把含上游 Key 的配置复制到 Linux，也不要在未核对账户侧调用量前签发替代会话。

不要只为让恢复命令通过而清空或重建生产账本。请求次数账本不是 token 费用账本；服务提供方侧成本核对、TLS、部署认证、密钥轮换和灾备加密/异地复制仍需单独建设。备份恢复完成不改变 Hub 写入、Worker、Scheduler 或 Web 执行开关；它们保持关闭，直至部署文档列出的真实验收全部通过。
