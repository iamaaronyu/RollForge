# Worker PostgreSQL 回归补充

日期：2026-10-27。在远端 Linux 部署 checkout `059fc81` 上，使用真实 PostgreSQL 服务重新运行以下四组集成测试：

- `tests/integration/test_execution_postgres.py`
- `tests/integration/test_job_api_postgres.py`
- `tests/integration/test_registry_postgres.py`
- `tests/integration/test_worker_postgres.py`

结果：**33 passed、1 skipped，13.68 秒**。Worker 用例中租约领取、验证前拒绝、过期检查和幂等完成均在真实 PostgreSQL 上通过。每个用例通过共享 fixture 创建随机 `test_execution_<uuid>` schema，迁移仅应用于该 schema，结束时自动删除；事后查询确认遗留测试 schema 数量为 **0**。

唯一跳过项依赖独立固定版本 MinIO 二进制。此次没有对线上 Silo bucket 做写入，没有启动 Worker/Scheduler、Harbor、E2B Sandbox 或模型请求。Hub/Web 仍为 active，Scheduler 为 inactive；Hub 控制面写入与执行开关仍为 false。因此这项结果补充了 Worker 数据库租约回归，不能替代真实 Silo Worker 提交、双 Worker、Sandbox 中断恢复或多 Trial 端到端验收。

## 2026-10-27 租约持有进程强制终止恢复

在 Linux 节点使用单独的 PostgreSQL 16 临时容器（只绑定 `127.0.0.1`）运行 `tests/integration/test_execution_postgres.py`，结果 **13 passed**。新增验收由独立子进程运行真实 `Worker.once()` 代码，通过测试 Hub 连接真实 PostgreSQL 领取 1 秒租约，再阻塞在执行入口；父测试进程发送 SIGKILL。测试确认 Worker 已创建权限为 0600 的本地 lease spool，再由新的执行服务回收并重新领取。连续两次强制终止后验证 Execution ID 与 fencing token 递增、旧 Execution 提交被拒绝、达到 `max_executions=2` 后 Trial 转为 FAILED 且不再可领取。临时容器在测试结束时自动删除，所有随机测试 schema 由 fixture 清理。

这项证据覆盖真实 `Worker.once()` 执行期间进程突然死亡后的 PostgreSQL 租约恢复与有界 Retry；测试 Hub 和执行方法被替换为数据库适配器与阻塞入口，没有启动正式 Scheduler、Hub HTTP 服务、Harbor、E2B、Sandbox 或模型调用。因此它不代表 Worker SIGKILL 后 Harbor Sandbox 能及时清理，也不替代双 Worker 多 Trial 主链和主机重启恢复验收。
