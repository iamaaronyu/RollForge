# Multi-Trial Job 验收记录

日期：2026-10-26。范围为 Job 样本数、确定性 Trial 展开、查询分页、Execution 历史和前端入口。Job 冻结单一快照；`attempt_index` 表示独立样本，Retry 在原 Trial 上增加 Execution。

## 实现

- `trial_count` 默认为 1，边界为 1–1000；创建事务内按稳定 ID 展开 Trial，幂等重放不重复创建，快照或样本数不一致时冲突。
- 移除每 Job 仅一个 Trial 的约束，增加 `(job_id, attempt_index)` 唯一约束、计数约束及 Job 级 Execution 序号。历史单 Trial 数据迁移时回填 Job/Execution 字段；若降级会丢失多样本数据则中止。
- USER API/SDK 提供 Trial 游标分页；Job 列表与详情提供总数和终态样本数。历史 Execution 游标按 Job 级递增序号排序，重试晚到时不会改变旧分页位置，并保留样本本地 fencing token。
- 中文任务页允许选择样本数并查看样本、Execution 和状态。

## 验证

- `make check`：172 项通过、1 项既有依赖警告；包含 Python lint/格式、测试、OpenAPI 导出和 TypeScript 生成一致性、前端类型检查与生产构建。
- `make runtime-check`：9 项通过。
- 真实 PostgreSQL：多样本创建和幂等、输入冲突、摘要计数、分页与用户隔离、样本领取、完成、过期回收及同 Trial Retry 通过；旧版单样本数据升级回填及有数据时拒绝破坏性降级通过。
- Linux `100.106.244.122`：Ubuntu 22.04、内核 `6.8.0-138-generic`、KVM 可用，E2B API/orchestrator 健康；当时可用内存约 3 GiB。仅做状态检查，没有启动新 Sandbox。

## 未完成的验收

本轮没有重跑真实多 Trial Worker → Harbor → E2B → 模型 → 对象存储 → Hub 全链。当前本机没有本轮有效的受限模型会话文件和 Worker/Hub 专用验收凭证，无法安全启动受预算限制的真实调用。已有单 Trial 真实主链记录不能替代多样本验收。后续需在并发 1 下创建两个独立样本，分别执行并核对结果、产物作用域和计数；再让其中一个 Trial Retry，证明样本数不变且多出一个 Execution。验收期间不得把上游模型 Key 复制到 Linux 或 Sandbox。

Registry 的不可变版本尚未解析为运行绑定，Scheduler 持续消费、背压和宿主故障恢复也未实现。`platform.execution_enabled` 保持关闭。本记录不表示多 Trial 真实执行链或生产运行已通过。
