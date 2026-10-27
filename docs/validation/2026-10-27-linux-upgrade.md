# Linux `d209366` 升级验收

## 发布结果

- 目标节点：`100.106.244.122`。
- 服务 checkout：`/opt/rollforge`，当前提交 `d209366`；升级前提交为 `a613c05`。
- Hub 与 Web 为 `active`；Hub `/health/ready` 和 Web 首页均返回 HTTP 200；Scheduler 为 `inactive`。
- `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED`、`ROLLFORGE_WORKER_RUNS_ENABLED`、`ROLLFORGE_SCHEDULER_ENABLED`、`ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS` 均保持 `false`。
- 升级差异不含 Alembic migration、Python/Node 依赖锁文件或 Runtime 依赖变化。`alembic upgrade head` 成功；本次不产生 schema 迁移。
- 隔离 checkout 完整 `make check`：191 passed、54 skipped；OpenAPI、Web 类型检查和生产构建通过。54 项跳过包含未配置独立 MinIO fixture 的集成测试；真实 PostgreSQL 16 + Silo API 外部轨迹验收另见[外部子轨迹记录](2026-10-27-external-subtrajectory.md)。`make runtime-check`：9 passed。

## 部署与恢复记录

先在隔离 checkout 构建并验收。目录切换试运行发现 Python console script 的 shebang 保留了临时目录路径，随后尝试的即时 Web 探测又早于服务监听。自动回退脚本在目录缺失情形下未恢复旧服务路径，造成短时服务中断；没有开启任何写入或执行开关，也没有执行任务。通过保留的 Git 对象恢复 `a613c05`，重建环境后验证 Hub/Web 均返回 200。

之后在原固定路径 `/opt/rollforge` 完成部署：确认 checkout 干净、目标提交可用、无迁移或依赖锁差异；停止 Hub/Web，切换至 `d209366`，在最终服务路径用 `uv sync --reinstall` 重建 Python console script，重建 Web，执行迁移命令并对 Hub/Web readiness 做有界重试。最终 Hub 与 Web 均 active 且返回 200，Scheduler inactive，所有门仍关闭。临时构建目录已删除；`/opt/rollforge-recovery-a613` 保留旧版本源码作为恢复参考，不包含服务凭证。

本记录证明此次代码升级后服务可启动，不代表多 Trial Harbor/E2B 任务、备份读回、宿主掉电恢复或公开生产开放验收通过。正式执行继续关闭，直到[部署手册](../deployment-linux.md)列出的剩余验收完成。
