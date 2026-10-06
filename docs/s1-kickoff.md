# S1 加速开发切片

日期：2026-10-06。本文给出可执行的开发顺序。前三个切片已完成当前单 Trial 范围的数据库实现与验收，详见 [S1 数据库验收](validation/2026-10-06-s1-postgres.md)；第四个切片已完成 [鉴权 API 与 SDK 验收](validation/2026-10-06-s1-api.md)。完整 S1 和 S0 尚未完成。

## 开发与开放入口分开

按用户授权，调整开发顺序为：S0 收口期间允许开发、验证 S1 的控制面能力；Hub 真实执行入口仍须等待 S0、安全鉴权及 S1 核心验收全部通过。开发并行不改变阶段验收标准。

S0 工作继续以 isolation-recovery 验收记录为准，避免重复已经通过的专项检查。模型网关 Sandbox 连通性、重复清理和主机重启复检分别收集证据。

## 第一条交付链

固定一个 Task、Agent、Model，先完成单 Job、单逻辑 Trial 的数据库闭环，再接真实 Worker。默认并发 1，但竞争验收必须使用两个独立执行者。

| 切片 | 修改位置 | 必须交付的结果 |
|---|---|---|
| 1：领域契约 | packages/schemas | Job/Trial/Execution 请求与响应；不可变 Revision 和执行配置快照；租约、fencing、终态与 Retry 语义 |
| 2：持久化 | apps/hub-api 的模型和 Alembic 迁移 | Job、Trial、Execution 表；唯一约束、索引、外键；迁移可回退；快照不得包含凭证 |
| 3：事务服务 | apps/hub-api | 创建、领取、续租、完成、过期回收；状态变化调用共享状态机；同一事务校验所有权、token 与有效期 |
| 4：鉴权 API | apps/hub-api、packages/hub-sdk | 创建与查询 Job，Worker 领取/续租/提交接口；访问权限和 Worker 权限分离；重复请求及冲突有明确语义 |
| 5：真实执行 | services/worker、packages/harbor-adapter | 控制面调用独立固定版本 Runtime；Harbor 管理 Sandbox；上传原始结果和 Manifest 到对象存储后提交元数据 |
| 6：最小页面 | apps/hub-web | 从 OpenAPI 生成类型；创建单 Trial Job、显示状态、结果与失败信息 |

每个切片形成可审查的独立改动。通用 Registry、批量展开、复杂调度和实验比较后置；不后置鉴权、事务租约或 fencing。

## 先验证执行权，再接 Sandbox

切片 2、3 使用真实 PostgreSQL，并在专用测试数据库中验证：

1. 两个独立事务竞争领取同一 Trial，只有一个获得有效执行权。
2. 租约过期后回收创建新 Execution，Trial 和 Attempt 数量不增加。
3. 原 Execution 的迟到续租、完成和失败提交均被拒绝；不能修改新 Execution。
4. 相同完成提交可幂等重放；相同标识但不同结果返回冲突。
5. Worker 中断后任务记录仍存在于 PostgreSQL；恢复次数受预算限制。
6. 评分 0 是有效结果，基础设施故障不能冒充已评分终态。

租约时间使用数据库时间。结果提交必须在一个事务中完成所有权校验和终态写入，不能先检查再无条件更新。上传完成和数据库提交之间的失败需要可恢复；对象键不得允许旧 Execution 覆盖新结果。

这里的数据库测试不算真实 Harbor/E2B/模型验收。切片 5 必须读取 Harbor 0.24.0 的真实源码，并再次取得主链证据。

## 检查与发布

开发阶段运行相关检查；提交前运行 make check，运行环境改动追加 make runtime-check。数据库验收失败或环境缺失时明确记录，不能用 SQLite 或 Mock 作为并发验收替代。

前端和 Worker 共同消费 packages/schemas 与已确认的 OpenAPI；接口确定后再对接，避免独立发明状态字段。保留现有未提交的 S0 修改，不混入 S1 提交。

开放真实执行入口前，确认 S0 剩余关卡、鉴权、数据库竞争与恢复、对象存储结果恢复及真实单 Trial 全链验收均通过。
