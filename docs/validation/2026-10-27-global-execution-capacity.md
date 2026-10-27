# PostgreSQL 全局执行容量验收

日期：2026-10-27。验收提交：`23d0701`（容量限制实现）与 `6a62149`（FIFO 恢复顺序断言）。测试在 Linux 节点的隔离临时 checkout 中运行，没有更新 `/opt/rollforge` 或重启正式服务。

## 实现

新增 `ROLLFORGE_GLOBAL_EXECUTION_LIMIT`，范围为 1–256，默认值为 1。Scheduler/Hub 领取 Execution 时，在 PostgreSQL 事务内取得共享 advisory lock，再计算 `RUNNING` Execution 数量并决定是否领取。共享同一数据库和配置的多个 Hub 进程由数据库串行化容量检查；过期租约在回收改变其状态前仍占用容量。状态完成后即可释放槽位。`/api/v1/platform` 返回当前限制值，部署预检验证配置范围。

## 验收结果

- 本地完整 `make check`：**240 passed、59 skipped**；`make runtime-check`：**9 passed**。ruff、格式、OpenAPI 检查、前端类型检查和 Next.js 生产构建通过。集成测试跳过项需要可选的真实数据库/对象存储/历史 Harbor 样本环境。
- Linux 节点真实 PostgreSQL + 部署中 Silo 的完整 `tests/integration`：**60 passed、2 skipped，29.81 秒**。其中包括独立 `ExecutionService` 实例及独立 OS 进程竞争领取、两个独立 Hub HTTP 服务并发领取、全局上限阻止额外领取、完成后释放容量、过期租约在回收前占用容量，以及 FIFO 队列中 Retry 重新排队的行为。
- 新增 100 个逻辑 Trial 的真实数据库验收：Job 创建重放保持幂等；以 37 项分页完整遍历 0–99 并确认 ID 唯一；在全局领取容量 100 下同时发起 100 个领取请求，获得 100 个不同 Trial 的执行租约，快照相同，随后领取被容量上限阻止。该测试没有启动 Harbor/E2B Sandbox，证明的是 PostgreSQL Job 展开、分页和租约容量，不代表 100 路真实计算吞吐。
- 新增跨进程容量测试：两个 Python OS 进程在数据库连接就绪后通过屏障同时调用 `ExecutionService.claim()`，共享 PostgreSQL 容量上限为 1 时恰好一个进程取得租约；随后查询确认只有一个 `RUNNING` Execution。它验证数据库 advisory lock 的进程间行为，不启动两个 Hub HTTP 服务。
- 新增双 Hub HTTP 测试：两个独立 Uvicorn 进程各自加载独立 FastAPI Hub 应用、不同 Worker 身份和相同的 PostgreSQL 隔离 schema；同时调用 Worker claim API 后，结果为一项 HTTP 200 与一项 HTTP 204，数据库只有一个 `RUNNING` Execution。此测试在临时环境中显式开启写入门，正式部署的写入/执行门保持关闭。
- 测试仅使用随机隔离 PostgreSQL schema 与随机 `rollforge-test-*` bucket。运行前后同名前缀 bucket 数均为 **0**，测试遗留 bucket 数为 **0**，`test_execution_*` schema 残留数为 **0**。
- 跳过项为共享 Silo 服务重启测试（不重启正式共享服务）和缺少历史 Harbor 原生输出样本的测试。

## 范围与限制

该验收证明同进程、独立 OS 进程及两个 Hub HTTP 进程在共享 PostgreSQL 上遵守全局领取容量，并验证容量释放、过期回收边界、FIFO Retry 和 100 个逻辑 Trial 的数据库排队边界。它没有启动常驻双 Worker，也没有进行两路或 100 路 Harbor/E2B 任务、Worker/Scheduler 进程重启、宿主掉电或网络分区演练。现阶段正式执行门保持关闭；全局并发仍应按服务器资源设置，默认值为 1。
