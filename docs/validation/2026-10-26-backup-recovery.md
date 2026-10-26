# 跨组件备份与恢复切片验收

日期：2026-10-26。新增 `scripts/backup_rollforge.py` 与 [操作手册](../backup-recovery.md)，覆盖 PostgreSQL 逻辑 dump、当前 S3 bucket 对象、模型网关 SQLite 请求次数账本的私有备份与隔离恢复。

## 已实现

- 备份前要求确认 Hub 写入、Worker、Scheduler 与网关已停止。只写入新建 0700 目录，文件为 0600；数据库连接密码经临时 0600 PGPASSFILE 传给 PostgreSQL 工具，不出现在进程参数或 CLI 输出。网关 JSON 配置和上游模型 Key 不进入备份包。
- 每个 S3 对象保存原 key、元数据、大小及 SHA-256；备份和恢复均流式读写并逐项读回校验。数据库引用的 Task/Revision key 必须存在；已提交 Execution Manifest 的对象内容摘要须匹配 PostgreSQL 中的 `manifest_digest`。
- PostgreSQL 备份记录 Job、Trial、Execution、Asset、Revision、迁移版本逐行 JSONB 摘要与状态分布；恢复到隔离空数据库后重新计算并比较。Revision 摘要、任务状态、结果 Manifest key/digest 均由这些行摘要覆盖。恢复不删除目标库，不覆盖异内容对象；对象写入在前、数据库在后，部分对象上传可安全校验后重试。
- 网关预算文件使用 SQLite Online Backup。恢复到仍有现存账本的主机时，对每个 token hash 取 `max(现有 used_requests, 备份 used_requests)` 并原子替换，保留较新记录；检测到 WAL/SHM/journal sidecar 时拒绝恢复。新主机账本丢失时必须配置无活动会话的 gateway JSON，恢复后不创建或签发替代会话；账户侧用量核对前不会开放模型调用。

## 验证与未验收

`tests/unit/test_backup_rollforge.py`：8 passed。使用真实临时 SQLite 数据库验证在线快照、同机计数单调合并、新主机需撤销全部会话、网关未停止时拒绝恢复、包摘要篡改检测、对象引用/Manifest 摘要检查、PostgreSQL 密码文件权限与恢复目标隔离。`scripts/backup_rollforge.py --help` 与 Python 编译检查通过。

当前本机 Docker CLI 无法连接 Docker daemon；本机没有 `pg_dump`、`pg_restore`、`psql`、MinIO 或 `mc` 可执行文件，且本切片不在远端安装服务或操作 E2B。因此没有运行真实 PostgreSQL 自定义归档 dump/restore 或真实 MinIO bucket 读回；以上 unit 用例不是这些集成验收的替代。完整 `make check` 与 `make runtime-check` 结果以本次最终执行记录为准。

## 明确边界

备份包是明文，操作手册要求只写入加密文件系统；文件权限不等于静态加密。数据库全局角色、所有者/ACL、PostgreSQL 服务设置、S3 bucket 策略/身份和对象版本历史不在包中。对象引用与 Revision/Execution 数据的数据库行摘要会比对，但仍需在独立真实 PG/MinIO 环境做一次创建、恢复和业务只读读回验收。真实 Sandbox 恢复、网关 TLS/部署认证、异地加密复制、恢复演练告警和正式执行开关仍未完成；三个执行/写入开关保持关闭。
