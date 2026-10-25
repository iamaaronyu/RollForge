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
