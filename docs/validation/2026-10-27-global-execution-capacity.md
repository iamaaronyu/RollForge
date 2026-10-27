# PostgreSQL 全局执行容量验收

日期：2026-10-27。验收提交：`23d0701`（容量限制实现）与 `6a62149`（FIFO 恢复顺序断言）。测试在 Linux 节点的隔离临时 checkout 中运行，没有更新 `/opt/rollforge` 或重启正式服务。

## 实现

新增 `ROLLFORGE_GLOBAL_EXECUTION_LIMIT`，范围为 1–256，默认值为 1。Scheduler/Hub 领取 Execution 时，在 PostgreSQL 事务内取得共享 advisory lock，再计算 `RUNNING` Execution 数量并决定是否领取。共享同一数据库和配置的多个 Hub 进程由数据库串行化容量检查；过期租约在回收改变其状态前仍占用容量。状态完成后即可释放槽位。`/api/v1/platform` 返回当前限制值，部署预检验证配置范围。

## 验收结果

- 本地完整 `make check`：**240 passed、56 skipped**；ruff、格式、OpenAPI 检查、前端类型检查和 Next.js 生产构建通过。56 项跳过需要可选的真实数据库/对象存储/历史 Harbor 样本环境。
- Linux 节点真实 PostgreSQL + 部署中 Silo 的完整 `tests/integration`：**57 passed、2 skipped，22.67 秒**。其中包括两个独立 `ExecutionService` 实例竞争领取、全局上限阻止额外领取、完成后释放容量、过期租约在回收前占用容量，以及 FIFO 队列中 Retry 重新排队的行为。
- 测试仅使用随机隔离 PostgreSQL schema 与随机 `rollforge-test-*` bucket。运行前后同名前缀 bucket 数均为 **0**，测试遗留 bucket 数为 **0**，`test_execution_*` schema 残留数为 **0**。
- 跳过项为共享 Silo 服务重启测试（不重启正式共享服务）和缺少历史 Harbor 原生输出样本的测试。

## 范围与限制

该验收证明多个服务实例在共享 PostgreSQL 上按配置执行全局领取容量约束，并验证容量释放、过期回收边界与 FIFO Retry。它没有启动常驻双 Worker，没有进行两路并行 Harbor/E2B 任务、Worker/Scheduler 进程重启、宿主掉电或网络分区演练。现阶段正式执行门保持关闭；全局并发仍应按服务器资源设置，默认值为 1。
