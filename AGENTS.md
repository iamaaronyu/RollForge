# RollForge 开发规则

- 开发前读取 docs/architecture.md 和 docs/development.md。
- 文档和面向用户的说明默认使用中文；API、代码标识符和协议名称保持稳定。
- 共享领域契约只定义在 packages/schemas。业务 API 实现后从 OpenAPI 生成前端类型，不独立发明状态字段。
- 修改集成前读取固定版本 Harbor 的真实源码，不虚构接口，不 Fork Core。
- Harbor 负责 Sandbox 生命周期；平台只提供经过验证的配置和扩展。
- 区分逻辑 Trial/Attempt 与物理 Execution/Retry。
- 状态变化必须经过状态机，并在事务中校验租约所有权。
- PostgreSQL 是权威状态源；Redis 不能成为唯一持久化任务记录。
- 轨迹、产物、日志等大对象放入对象存储，不直接放入数据库行。
- 凭证不能出现在配置快照、日志、公开测试样本或提交中。
- 主链集成改动需要真实 Harbor/E2B/模型验收；Mock 不算真实验收。缺少环境时明确记录。
- 提交前运行 make check；运行环境改动同时运行 make runtime-check。不要自动修复无关用户改动。
- 用户已授权定期正常 commit/push。禁止 force push、重写历史、发布原始内部规划文件或无人值守解决冲突。
