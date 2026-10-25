# Registry 资产页验收记录

日期：2026-10-26。范围为用户私有资产发现、不可变版本登记/浏览，以及从已登记版本创建受审核 Job。

## 交付与边界

- 新增 `GET /api/v1/registry/assets`：仅列出认证 USER 自有资产，按 UUID 游标分页，每页 1–100 条，并返回资产类型和最新版本号。Worker 访问返回 403；其他 USER 只得到自己的空列表，不会获知他人资产。
- `/registry` 页面支持创建 Task、Agent、Model revision，列出资产及最近 100 个版本，并基于选中的 Task/Agent/Model revision 提交 Job。Job 的执行快照由 Hub 生成并固定注册引用。
- Task 归档须由运维人员先部署到审核存储路径；页面仅登记归档 key 与摘要，不提供上传。服务端继续校验 revision 所有权/摘要和完整部署审核快照；未审核的组合被拒绝。
- Hub token 仅由 Next 服务端持有。页面使用单用户静态 USER token，不是登录系统；Web 实验开关关闭时不能通过页面创建 Job。

## 验证

- 完整 `make check`：170 passed、16 skipped、1 项现有 Starlette/httpx 弃用警告。所有配置了真实 PostgreSQL 的迁移、执行、Job、Registry、查询及 Worker 测试均通过；其中 Registry 新旧 API 覆盖所有权、角色、分页、revision 摘要、部署审核匹配和绕过拒绝。
- 16 项跳过：4 项产物查询、8 项独立 MinIO、2 项 MinIO/数据库联合恢复、1 项 Worker 存储提交依赖、1 项历史原生轨迹样本；本轮未提供固定 MinIO 测试二进制和该历史样本。该边界不替代以往 MinIO/对象存储验收。
- OpenAPI 离线导出一致，前端类型检查及 Next.js 生产构建通过；动态路由包含 `/registry`。本轮没有启动 Harbor/E2B、Worker 或模型请求。
- 使用临时 PostgreSQL 容器完成后已删除容器并关闭 SSH 隧道；没有修改 Linux 持久服务或运行数据。

## 尚未覆盖

多用户登录、资产分享/审批工作流、Task 归档上传、超过 50 个资产的 UI 翻页交互、超过最近 100 个 revision 的页面选择，以及真实多 Trial Harbor/E2B/DeepSeek 运行仍需后续完成。
