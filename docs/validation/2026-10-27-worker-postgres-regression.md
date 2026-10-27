# Worker PostgreSQL 回归补充

日期：2026-10-27。在远端 Linux 部署 checkout `059fc81` 上，使用真实 PostgreSQL 服务重新运行以下四组集成测试：

- `tests/integration/test_execution_postgres.py`
- `tests/integration/test_job_api_postgres.py`
- `tests/integration/test_registry_postgres.py`
- `tests/integration/test_worker_postgres.py`

结果：**33 passed、1 skipped，13.68 秒**。Worker 用例中租约领取、验证前拒绝、过期检查和幂等完成均在真实 PostgreSQL 上通过。每个用例通过共享 fixture 创建随机 `test_execution_<uuid>` schema，迁移仅应用于该 schema，结束时自动删除；事后查询确认遗留测试 schema 数量为 **0**。

唯一跳过项依赖独立固定版本 MinIO 二进制。此次没有对线上 Silo bucket 做写入，没有启动 Worker/Scheduler、Harbor、E2B Sandbox 或模型请求。Hub/Web 仍为 active，Scheduler 为 inactive；Hub 控制面写入与执行开关仍为 false。因此这项结果补充了 Worker 数据库租约回归，不能替代真实 Silo Worker 提交、双 Worker、Sandbox 中断恢复或多 Trial 端到端验收。

## 2026-10-27 Scheduler 进程重启恢复

在 Linux 节点使用单独的 PostgreSQL 16 临时容器（只绑定 `127.0.0.1`）运行 `tests/integration/test_execution_postgres.py`，结果 **13 passed**。新增验收启动独立 Scheduler 子进程；其真实 `Worker.once()` 经本机 Uvicorn Hub 和 Worker 鉴权访问 PostgreSQL，领取 300 秒租约并创建权限为 0600 的 spool，随后阻塞在测试执行入口。父测试进程 SIGKILL Scheduler 后将租约过期时间前移，再启动新的 Scheduler；由新进程恢复循环通过 Hub API 回收租约、报告回收计数并领取 Retry。再次强杀后，第三个 Scheduler 回收最后一次 Execution，Trial 进入 FAILED 且不再领取。测试还验证 Execution ID 与 fencing token 递增、旧 Execution 经 Hub HTTP 提交返回 409。临时容器在测试结束时自动删除，所有随机测试 schema 由 fixture 清理。

这项证据覆盖真实 Scheduler 与 `Worker.once()` 进程突然死亡后的 PostgreSQL 租约恢复和有界 Retry；Hub 使用临时本地 Uvicorn 服务和独立测试 Worker token，Runtime 执行入口替换为阻塞函数，没有启动 Harbor、E2B、Sandbox 或模型调用。租约过期由测试数据库显式前移，避免等待生产 300 秒租约。因此它不代表 Scheduler 主机掉电恢复、Worker SIGKILL 后 Harbor Sandbox 清理，也不替代双 Worker 多 Trial 真实主链验收。
