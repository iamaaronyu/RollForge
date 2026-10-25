# FIFO 调度与租约回收验收记录

日期：2026-10-26。范围为单调度进程的有界持续 Worker、FIFO 样本队列和过期租约自动回收。调度和执行默认关闭；本轮没有启动真实模型任务。

## 实现

- 新增常驻 `rollforge_scheduler` 进程。启动需同时显式启用 `ROLLFORGE_SCHEDULER_ENABLED` 与 `ROLLFORGE_WORKER_RUNS_ENABLED`，并配置既有 Worker、Hub、S3、网关与私密 Runtime 会话。
- 并发槽位为每进程 1–16（默认 1）；空队列轮询，失败指数退避并限制最大等待 30 秒。Worker 与租约回收 API 共用连接客户端。
- SIGTERM/SIGINT 停止新领取，优先等待执行结束（默认宽限 300 秒，可配置）；超时后取消 Worker。既有 Worker 会向 Runtime 发送 SIGTERM 并等待 Harbor 清理。
- `trials.queued_at` 记录首次入队与过期 Retry 重新入队时间。领取排序按入队时间、Job ID、attempt_index；使用既有 PostgreSQL `FOR UPDATE SKIP LOCKED` 防止重复领取。旧样本由迁移时刻回填，平局时采用稳定 ID/样本序号顺序。
- 新增 WORKER 鉴权的 `/api/v1/worker/leases/recover` 与 SDK 方法，回收批次限制 1–1000、默认 100。服务端事务重新核验数据库时钟与状态；预算内过期 Trial 排队到队尾，达到上限则 FAILED。USER 无此权限。

## 验证

- `make check`：181 passed、2 skipped、1 条既有 Starlette/httpx 弃用警告；Python 检查、OpenAPI/前端类型同步、TypeScript 检查及 Next.js 生产构建通过。
- 2 项跳过均为需要本地历史 Harbor 原生轨迹/输出文件的旧验收样本；本轮没有提供这些运行产物。其余真实 PostgreSQL、MinIO 用例均运行。
- 真实 PostgreSQL 执行/Job API 集成：18 passed，包含 0004 迁移及元数据比较、FIFO 次序、SKIP LOCKED 竞争、租约回收 API、Retry fencing 和迁移回退。
- `make runtime-check`：9 passed；`make scheduler-check`、`make worker-check` 导入检查通过。
- 调度器单测覆盖默认关闭、并发上限、恢复循环、空闲/失败行为、停止时排空与超时取消清理。

## 边界

本轮未在 Harbor/E2B 上启动两个真实并发 Worker，也未做进程崩溃或主机重启运行演练；旧的单 Trial 真实链路不能替代这些验收。并发上限目前是每个调度进程本地额度，多进程集群没有共享全局容量/leader 机制，部署必须限制实例数量并按并发总和预算。`platform.execution_enabled` 保持 false。
