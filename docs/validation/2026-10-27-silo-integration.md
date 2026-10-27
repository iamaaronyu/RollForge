# 真实 Silo 对象存储与 Hub 提交恢复验收

日期：2026-10-27。使用刚推送代码 `a070f06` 的临时 checkout，对部署中 loopback Silo 执行隔离集成测试；测试结束后删除临时 checkout，没有更新 `/opt/rollforge` 或重启服务。

## 验收结果

运行 `tests/integration/test_object_store_minio.py`、`tests/integration/test_storage_recovery_postgres.py` 与 `tests/integration/test_worker_postgres.py`，结果为 **14 passed、2 skipped，6.17 秒**。其中 9 项实际访问 Silo：

- 条件写竞争只有一个写入者获胜；响应丢失后用新客户端恢复上传。
- 在首个文件写入后及 Manifest 写入后分别终止独立上传子进程，再验证重试或远端恢复。
- 篡改/缺失对象被拒绝；Retry 使用独立 Execution 对象命名空间。
- Hub 鉴权完成提交前核验 Silo 中的 Manifest 与产物；已接受响应丢失后可幂等恢复，旧 Execution 在租约过期及 Retry 后被拒绝。
- PostgreSQL 测试只在随机 schema 中运行；事后查询确认残留测试 schema 数量为 **0**。

测试通过受限配置文件读取 Silo 管理员身份，只创建随机 `rollforge-test-<uuid>` bucket。运行前后核对得到：既有同名前缀 bucket **0**，本次未清理 bucket **0**；现有 bucket、对象和服务均未变更。两项跳过分别是外部 Silo 重启用例（刻意不重启共享服务）和缺少历史 Harbor 原生输出样本用例。

## 验收范围

这次直接验收了 Silo 的 S3 条件写入、对象读回、摘要校验和上传/提交恢复。Worker 相关用例通过真实 PostgreSQL、Silo 和 Hub API ASGI 应用测试存储提交及租约边界，但没有启动常驻 Worker/Scheduler，也没有调用 Harbor、E2B、Agent 或模型。Hub/Web 保持 active，Scheduler inactive；Hub 写入和执行门为 false。它补足了 Silo 兼容性证据，不能替代真实双 Worker、多 Trial 运行、E2B 故障恢复或 Silo 容器重启验收。
