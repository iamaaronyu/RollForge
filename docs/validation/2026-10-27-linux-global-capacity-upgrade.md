# Linux 全局容量版本升级验收

日期：2026-10-27。目标节点：`100.106.244.122`。固定服务目录：`/opt/rollforge`。

## 升级前检查

- 服务 checkout 干净，旧版本 `059fc8134a3d1c8a7f282466a7363ff77e6e1fc1`。目标提交 `7099e66814f2d9b329873a746d69a2c10b9803b6` 已在隔离环境通过代码与真实数据库验收。
- Hub/Web 为 active，Scheduler 为 inactive；升级前 Hub readiness 与 Web 首页均为 HTTP 200。
- `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED`、`ROLLFORGE_WORKER_RUNS_ENABLED`、`ROLLFORGE_SCHEDULER_ENABLED` 与 `ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS` 均为 `false`。
- 旧版本到目标版本的差异不包含 Alembic 迁移、依赖锁文件、Harbor Runtime 或 Web 应用源码变更。目标版本仍使用默认全局执行容量 1。

## 升级结果

停止 Hub/Web 后，将固定目录检出至 `7099e66`，在原路径执行 `uv sync --all-packages --locked --python 3.12`，确认 RollForge systemd unit，并运行 `rollforge_scheduler --check`。随后启动 Hub/Web。没有执行数据库迁移，也没有启用 Scheduler 或写入/执行开关。

最终核验：Hub/Web 均为 active，Scheduler 为 inactive；Hub readiness 与 Web 首页均为 HTTP 200；上述四个门仍为 `false`；`/opt/rollforge` 工作区干净，当前提交为 `7099e66`。升级期间没有运行 Rollout 任务，未执行回退。

`systemd-analyze verify` 返回成功，同时报告系统中其他 unit 的既有警告（`xray.service` 的 `nobody` 用户配置与 `snapd.service` 的未知 `RestartMode` 键）；RollForge units 未报告校验错误，Hub/Web 已通过启动和 HTTP 健康检查。

## 验收范围

本次部署包含已验收的 PostgreSQL 全局领取容量控制。Linux 完整集成套件为 58 passed、2 skipped；本地 `make check` 为 240 passed、57 skipped，`make runtime-check` 为 9 passed。100 个逻辑 Trial 的真实 PostgreSQL 展开、分页和租约领取已通过，但没有启动 Harbor/E2B Sandbox，也没有开启真实双 Worker 或正式任务写入。加密备份读回、双 Worker/进程故障恢复及执行门开放仍未验收。
