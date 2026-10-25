# Registry 运行绑定验收记录

日期：2026-10-26。范围为把用户私有 Task/Agent/Model Revision 安全解析为现有 Worker RunnableBinding，并阻断绕过部署审核的运行配置。

## 实现与权限边界

- Job 请求提交三类 `RevisionRef`。Hub 按认证用户查找版本，核对所有权、revision、持久化 digest 与规范化 spec 的重算 digest；资产缺失/跨用户返回 404，摘要不匹配返回 409，类型不匹配返回 409。
- Hub 将注册 Task archive key/digest、Agent 名称/版本、Model 名称/协议/网关/凭证模式与启动时加载的私密部署审核目录逐项比较。完全匹配才使用部署快照中的 RunnableBinding；未审核组合返回 403。
- `ExecutionSnapshot.registry` 保存用户选中的注册 spec 摘要引用；旧 `task.digest` 仍是 Worker 核验的任务内容摘要，Agent/Model digest 仍与 RunnableBinding 对齐。两类摘要不互相替代。
- 新增 `POST /api/v1/jobs/from-revisions` 与 SDK `create_job_from_revisions`。普通 `POST /api/v1/jobs` 只能创建元数据 Job，带 `runtime` 的请求返回 403。Registry 注册本身不表示资产获准执行。

## 验证

- 真实 PostgreSQL 目标测试：3 passed。成功路径创建双 Trial Job、幂等重放且快照固定两组摘要；其他用户访问私有 revision 返回 404；伪造摘要返回 409；未匹配部署审核配置返回 403；直接提交 runnable snapshot 返回 403。
- OpenAPI 与生成 TypeScript 类型已同步；全量 `make check`：184 passed、2 skipped、1 项 Starlette/httpx 弃用警告。两项跳过依赖未提供的历史 Harbor 轨迹/原生输出样本；本轮真实 PostgreSQL 与 MinIO 检查均执行。
- 本切片没有调用 Harbor/E2B/模型；模型调用仍需后续独立的受限会话验收。真实任务归档内容由 Worker 在下载与解包时继续复核。

## 尚未完成

Registry 管理/发现网页、Dataset revision、审核工作流和真实多 Trial Harbor/E2B/模型主链仍未完成；部署目录中的审核绑定仍需由运维者安全维护。正式执行开关保持关闭。
