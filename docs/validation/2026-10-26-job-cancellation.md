# Job 与 Trial 取消验收

## 已实现

- USER 可取消整个 Job 或其中一个 Trial；所有写请求仍受 `ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED` 保护，跨用户或无效身份不能取消。
- 取消操作使用 PostgreSQL 锁与事务。队列中的 Trial 直接进入 `CANCELLED`；运行中 Trial 的当前 Execution 同事务标记为 `CANCELLED`，因此续租、过期回收和完成提交都不能重新开放或覆盖终态。
- Worker 下一次 30 秒心跳续租失败后，既有取消路径向 Runtime 发送 SIGTERM 并等待 Harbor 清理。该接口不承诺立即终止，运行中 Sandbox 的真实取消时延仍待 E2B 验收。
- API/SDK、OpenAPI 及任务详情页的确认按钮已接通。网页仍要求显式打开实验写操作配置；服务端写入门默认关闭。

## 检查结果

- 本地 `make check`：142 passed、51 skipped；Python 检查、OpenAPI、Web 类型和生产构建通过。
- 临时 PostgreSQL 16（Linux Docker 容器，loopback 端口、768 MiB 上限）上的三个取消 API 测试通过，覆盖 Job 幂等批量取消、单 Trial 取消、权限、失效续租/完成拒绝、兄弟 Trial 继续可领取，以及取消与完成并发时恰有一个终态获胜。
- PostgreSQL 元数据对比、迁移完整回退及重放测试通过。
- 真实数据库测试经 SSH 隧道运行较慢；完整全库 `make check` 未在此环境等完。仅上述聚焦 PostgreSQL 测试计为通过。
- 临时 PostgreSQL 容器和 SSH 隧道均已关闭；现有 E2B 数据库和运行服务未修改。

## 尚待验收

需要真实 Worker/Sandbox 执行期间调用取消 API，确认续租撤销后 Firecracker 数量回到 0；还需覆盖 Worker 掉电、SIGKILL 和 Sandbox 清理失败。取消后的 Trial 当前为终态，不提供恢复或从原 Trial 重试。
