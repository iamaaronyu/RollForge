# Linux systemd 部署模板验证

## 本次变更

新增 Hub API 与单并发 Worker Scheduler 的 systemd unit、Linux 安装/升级/恢复手册。服务将 Hub 绑定到 loopback，Scheduler 并发设为 1；Worker 与 Scheduler 执行开关在受保护环境文件中显式保持关闭。Runtime 会话单独存放于 0700 目录、0600 文件，不向 Runtime 传递平台存储凭证或上游模型 API Key。

## 检查结果

- `make check`：141 passed、48 skipped；Python lint/format、OpenAPI 校验、Web 类型检查和生产构建通过。
- `make runtime-check`：9 passed。
- 目标 Linux 主机上的 `systemd-analyze verify` 成功解析两个 unit。因为该主机尚未安装 `/opt/rollforge/.venv`，语法检查时仅对 `/tmp` 临时副本将 `ExecStart` 替换为系统 `/usr/bin/true`；临时文件已删除。主机其他既有 systemd unit 输出权限和兼容性告警，与新增单元无关。
- 未在目标机安装或启用服务，未修改主机服务配置。

## 未完成验收

这不是 Hub/Worker 部署或真实主链验收。真实 PostgreSQL/对象存储/Hub 凭证、受限模型会话和生产 TLS 反向代理仍需准备；E2B/Harbor/模型的真实单 Trial 与重启恢复需按 [Worker 验证记录](../validation/2026-10-06-s1-worker.md) 和 [多 Trial 验收手册](../multitrial-acceptance.md) 完成。当前服务模板明确没有开放写入或真实执行。

## 2026-10-26 远程节点实际部署进展

目标节点：`100.106.244.122`。已将提交 `4a9f2fb` 正常推送至 GitHub，并通过 Git bundle 快进同步到 `/opt/rollforge`；服务器 checkout 无未提交改动。原 Compose 固定的 `minio/minio` 镜像已无法从 Docker Hub 拉取。现改用 digest 固定的 PGSTY Silo 社区兼容分支；其项目说明保留 S3 API、`MINIO_*` 配置和数据格式。Silo 是独立项目，镜像升级前仍须核对兼容性与安全公告。

- 完整部署预检通过，检查到凭证文件权限正确，Hub/Worker 对象存储凭证分离，写入、Worker、Scheduler 开关均关闭。
- RollForge PostgreSQL、Redis、Silo 均成功启动，仅发布到 loopback 端口 55432、56379、59000/59001；没有启动或改动 E2B 容器及其数据卷。
- 创建 RollForge 专用 bucket 与不同 IAM 用户：Hub 仅 `GetObject`，Worker 仅在该 bucket 内 `GetObject`/`PutObject`。真实 S3 请求验证 Worker 上传成功、Hub 读取成功、Hub 写入被拒。
- 数据库 Alembic 迁移成功。重启 PostgreSQL 容器后，迁移版本 `0005_cancelled_executions` 仍存在，Hub readiness 恢复为 HTTP 200。
- Silo 容器重启后，前述对象仍可读且字节一致。该项只验证容器重启，不等于整机断电恢复或备份恢复演练。
- 使用 Worker 短时会话发送 1 次无业务内容、`max_tokens=8` 的模型探测；可信网关日志记录 DeepSeek 上游 HTTP 200，未输出或保存模型响应。尚未验收预算耗尽、会话过期/撤销及账本重启恢复。
- `rollforge-hub.service` 和 `rollforge-web.service` 已启用并运行，分别只监听 `127.0.0.1:8000` 与 `127.0.0.1:3300`，readiness/Web HTTP 均为 200。`rollforge-scheduler.service` 仍 disabled/inactive；任务创建写入、真实执行和 Web 实验运行开关保持关闭。
- 代码验证：`make check` 为 169 passed、51 skipped；`make runtime-check` 为 9 passed。跳过项是需要额外真实 PostgreSQL、对象存储或运行数据的集成验收。

仍需完成：对象存储和 PostgreSQL 的独立备份及隔离环境读回演练；受限模型网关的已认证调用、预算与过期/撤销验证；整机重启后确认服务自恢复；然后在明确预算的合成任务下进行单 Trial Worker→Hub→Harbor/E2B→模型→对象存储全链及中断恢复验收。上述真实任务未验收前，三个写入/执行开关继续关闭。Silo 镜像基于 AGPLv3 社区分支；仓库尚未确定自身许可证，发布或再分发前需先完成许可证审查。
