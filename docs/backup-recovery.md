# RollForge 跨组件备份与恢复

本工具为 PostgreSQL、S3 兼容对象桶和模型网关 SQLite 请求账本生成同一恢复包。只有在 Hub 写入、Worker、Scheduler 和模型网关都停止后才能创建备份；PostgreSQL dump、对象列表/读取和 SQLite 快照不是跨系统原子事务，停止所有写入是它们构成同一恢复点的前提。

备份包及对象内容是明文，包含任务快照、执行结果和产物。备份目标必须位于加密卷或加密文件系统；目录权限 0700、文件权限 0600 只提供本机访问隔离，不等于静态加密。不要把备份包、网关配置、`.env` 或临时恢复目标提交到 Git。

## 创建和检查

安装 `pg_dump`、`pg_restore`、`psql`，创建仅 `rollforge` 可读写的加密备份目录。由管理员创建 `/etc/rollforge/backup.env`（`root:rollforge 0640`），其中包含 `ROLLFORGE_DATABASE_URL` 和 `ROLLFORGE_BACKUP_OBJECT_STORAGE_ENDPOINT`、`_BUCKET`、`_ACCESS_KEY`、`_SECRET_KEY`（变量名前缀均为 `ROLLFORGE_BACKUP_OBJECT_STORAGE`）。备份对象存储身份必须独立于 Hub/Worker：仅授予指定 bucket 的 `ListBucket` 与 `GetObject`，不授予写入、删除或其他 bucket 权限；Hub 的最小权限角色保持不变。不得将密钥写入命令行、shell history 或日志。网关账本路径取决于启动 `scripts/model_gateway.py --config ...` 时所用配置路径，预算数据库是该路径替换扩展名为 `.sqlite3` 后的文件。备份不复制含上游 Key 的网关 JSON 配置。

停写并停止服务后，由管理员通过受控终端运行：

```sh
BACKUP_ID=$(date -u +%Y%m%dT%H%M%SZ)
sudo systemd-run --wait --pipe --collect \
  --uid=rollforge --gid=rollforge \
  --property=WorkingDirectory=/opt/rollforge \
  --property=EnvironmentFile=/etc/rollforge/backup.env \
  /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/backup_rollforge.py create \
    --destination "/mnt/rollforge-backups/${BACKUP_ID}" \
    --gateway-budget-db /var/lib/rollforge/private/gateway.sqlite3 \
    --acknowledge-quiescent
```

`/mnt/rollforge-backups` 必须是已挂载的加密卷，且父目录归 `rollforge` 所有、权限 0700。预检会拒绝非私有目录、活动写入未确认的调用、缺失/损坏的 SQLite 账本和不安全对象存储 URL。创建成功后，用同一账号完整校验：

```sh
sudo -u rollforge /opt/rollforge/.venv/bin/python \
  /opt/rollforge/scripts/backup_rollforge.py verify \
  --bundle "/mnt/rollforge-backups/${BACKUP_ID}"
```

包内 `manifest.json` 保存 PostgreSQL 逻辑 dump、每个对象的 key/字节数/SHA-256、SQLite 账本和全部本地文件摘要。数据库指纹覆盖 Job、Trial、Execution、Asset、Revision、迁移版本的逐行规范化摘要，以及 Trial/Execution 状态汇总。创建时会确认数据库引用的 Task/Revision 对象存在，并逐一确认已提交 Manifest 对象内容与 PostgreSQL 中的 `manifest_digest` 相符。

## 恢复到隔离目标

先在隔离环境创建一个新的空 PostgreSQL 数据库和目标 S3 bucket。恢复目标连接必须用独立文件 `/etc/rollforge/restore-target.env` 提供：`ROLLFORGE_RESTORE_DATABASE_URL`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_ENDPOINT`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_BUCKET`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_ACCESS_KEY`、`ROLLFORGE_RESTORE_OBJECT_STORAGE_SECRET_KEY`；loopback MinIO HTTP 测试才可另设 `ROLLFORGE_RESTORE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP=true`。该文件建议 `root:rollforge 0640`。目标数据库名必须与源库不同；不要把源环境文件用于恢复。

目标 bucket 应为空。若上次恢复中途失败，工具允许其中已有对象为备份中完全相同的一部分，便于重试；多余或同 key 不同内容会拒绝。恢复先逐项上传并读回校验对象，再把 PostgreSQL 自定义归档通过单事务导入空库，最后复算数据库行指纹、状态分布、Revision 摘要及对象引用指纹。工具不会 `DROP`、清空或原地覆盖已有数据库/Bucket。由管理员启动：

```sh
sudo systemd-run --wait --pipe --collect \
  --uid=rollforge --gid=rollforge \
  --property=WorkingDirectory=/opt/rollforge \
  --property=EnvironmentFile=/etc/rollforge/restore-target.env \
  /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/backup_rollforge.py restore \
    --bundle "/mnt/rollforge-backups/${BACKUP_ID}" \
    --acknowledge-isolated-targets
```

恢复源与目标配置若同时存在，工具会拒绝源 PostgreSQL 同名数据库或完全相同的对象 endpoint/bucket。恢复后应在隔离环境核对数据库 fingerprint、对象数量与摘要、Hub 只读查询和就绪检查；确认后再由管理员更新应用环境文件指向恢复目标。PostgreSQL 全局角色/所有权/ACL、PostgreSQL 服务配置、S3 bucket 策略/用户、对象版本历史与删除标记均不在此逻辑备份范围内，需分别按部署配置恢复。恢复目标应预先设置正确的身份权限和加密策略。

## 网关请求预算

同一主机恢复时，停止网关后运行 `restore-budget`。它将备份账本和现存账本按 `token_sha256` 合并，计数取两者最大值；现存记录保留，不能通过旧备份把已用请求数减小。即使快照中的计数小于当前账本，该 token 仍保留当前较大计数；快照缺少的较新 token 记录也不会被删除。若现存账本有 SQLite WAL/SHM/journal sidecar 或结构损坏，工具会拒绝替换，需先调查数据库状态。

若原账本已丢失，旧快照无法知道快照之后已经消费的请求额度。新主机恢复要求网关配置权限正确且 `sessions` 为空；工具拒绝活动会话，也不会自动签发新会话。恢复后保持网关无会话，待模型账户侧预算和快照之后的调用量核对完成，再由授权操作者决定后续会话及额度；不能把旧配置/旧会话恢复回来，也不能在未知用量时重置请求预算。

```sh
sudo systemd-run --wait --pipe --collect \
  --uid=rollforge --gid=rollforge \
  --property=WorkingDirectory=/opt/rollforge \
  --property=EnvironmentFile=/etc/rollforge/restore-target.env \
  /opt/rollforge/.venv/bin/python /opt/rollforge/scripts/backup_rollforge.py restore-budget \
    --bundle "/mnt/rollforge-backups/${BACKUP_ID}" \
    --gateway-budget-db /var/lib/rollforge/private/gateway.sqlite3 \
    --acknowledge-gateway-stopped
```

新主机上账本文件缺失时，还必须在上述命令追加 `--gateway-config /var/lib/rollforge/private/gateway.json`；该配置的 `sessions` 必须为空。不要在未核对账户侧调用量前签发替代会话。

不要只为让恢复命令通过而清空或重建生产账本。请求次数账本不是 token 费用账本；服务提供方侧成本核对、TLS、部署认证、密钥轮换和灾备加密/异地复制仍需单独建设。备份恢复完成不改变 Hub 写入、Worker、Scheduler 或 Web 执行开关；它们保持关闭，直至部署文档列出的真实验收全部通过。
