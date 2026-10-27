# Worker PostgreSQL 回归补充

日期：2026-10-27。在远端 Linux 部署 checkout `059fc81` 上，使用真实 PostgreSQL 服务重新运行以下四组集成测试：

- `tests/integration/test_execution_postgres.py`
- `tests/integration/test_job_api_postgres.py`
- `tests/integration/test_registry_postgres.py`
- `tests/integration/test_worker_postgres.py`

结果：**33 passed、1 skipped，13.68 秒**。Worker 用例中租约领取、验证前拒绝、过期检查和幂等完成均在真实 PostgreSQL 上通过。每个用例通过共享 fixture 创建随机 `test_execution_<uuid>` schema，迁移仅应用于该 schema，结束时自动删除；事后查询确认遗留测试 schema 数量为 **0**。

唯一跳过项依赖独立固定版本 MinIO 二进制。此次没有对线上 Silo bucket 做写入，没有启动 Worker/Scheduler、Harbor、E2B Sandbox 或模型请求。Hub/Web 仍为 active，Scheduler 为 inactive；Hub 控制面写入与执行开关仍为 false。因此这项结果补充了 Worker 数据库租约回归，不能替代真实 Silo Worker 提交、双 Worker、Sandbox 中断恢复或多 Trial 端到端验收。
