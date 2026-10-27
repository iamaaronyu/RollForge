# 隔离 PostgreSQL 执行、Job API 与 Registry 回归

日期：2026-10-27。代码取自 Linux `/opt/rollforge` 的 `059fc81`；测试仅运行在同一 PostgreSQL 服务中新建的随机隔离数据库。

## 覆盖

- `tests/integration/test_execution_postgres.py`：迁移、执行状态、租约竞争、fencing、提交幂等与恢复边界。
- `tests/integration/test_job_api_postgres.py`：用户/Worker 鉴权、Job/Trial API 与数据库状态。
- `tests/integration/test_registry_postgres.py`：Registry Revision 写入竞争、所有权与冻结语义。

结果为 **30 passed**。测试数据库在 finally 阶段执行 `DROP DATABASE ... WITH (FORCE)`，随后只读查询 `rf_it_` 前缀数据库数量为 **0**。没有在生产 RollForge 数据库中创建 schema 或修改记录。

## 运行边界

没有运行 Worker、Scheduler、Harbor、E2B 或模型 API；不作为双 Worker、真实多 Trial 执行或主机故障恢复验收。此前的真实 Registry 双 Trial 平台闭环仍为并发 1 的一次运行。Hub 写入与执行开关保持关闭。
