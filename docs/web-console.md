# 本机单任务实验界面

页面入口：`/jobs`，详情入口：`/jobs/{job_id}`。显示用户范围内 Job、评分、失败原因和按 fencing token 排序的 Execution 历史；0 分属于有效评分，租约期限不代表完成时间。查询按游标分页，Job ID 顺序不代表创建时间。

## 服务端配置

| 配置 | 含义 |
|---|---|
| `ROLLFORGE_API_URL` | Next 服务端访问 Hub 的根地址；HTTP 仅 loopback，其他地址需 HTTPS |
| `ROLLFORGE_WEB_USER_TOKEN` | 与 Hub USER 角色匹配的私密 token；禁止 NEXT_PUBLIC 前缀 |
| `ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS` | 默认为关闭，只有 true 才允许页面创建 |
| `ROLLFORGE_APPROVED_TASKS_FILE` | Hub 启动时读取的私密 JSON 数组文件 |

Hub 写开关保持独立。缺少 token、审核任务、Hub 写开关或 Web 实验开关时，页面创建不可用。启动 Next 时绑定 loopback；在用户登录和部署权限完成前，不把此界面公开到局域网/公网，因为所有浏览器会共享服务端配置的同一用户身份。

Next 变量通过启动环境或 `apps/hub-web/.env.local` 配置，不能假设它自动读取仓库根目录 `.env`。私密配置不提交；根目录 `.env.example` 仅列出变量名称。Hub 读取自身启动环境或根目录 `.env`，审核目录路径未配置时应省略该变量，而不是设置为空路径。

审核目录文件权限 0600，保存在忽略目录。每项为共享 ApprovedTaskBinding：id、中文 label、ExecutionSnapshot。snapshot 必须包含符合已确认契约的 runtime 绑定；使用已审核、已上传且摘要固定的 Task，以及正确 Agent/Model Revision。禁止写凭证、任意环境变量或未验收网关。空目录表示禁用；重复 id、非法契约、链接、非普通文件或超过 1 MiB 的目录使 Hub 启动失败，错误不回显文件内容。配置变更需重启 Hub。

`GET /api/v1/tasks/approved` 仅返回 id/label。页面通过 Next Server Action 调用 `POST /api/v1/jobs/from-approved-task`，请求只有 job_id/task_id；完整快照由 Hub 查审核目录确定。job_id 在表单中固定，相同提交使用现有幂等语义。页面不提供任意快照编辑器。

## 查询与限制

`GET /api/v1/jobs?limit=20&after=<UUID>` 返回 JobList，只包含用户自己的 JobSummary 和 TrialView，不返回配置快照。`GET /api/v1/jobs/{job_id}/executions?limit=20&after=<token>` 返回 ExecutionList，按执行 token 递增，不返回 Worker 身份。limit 范围 1–100，游标来自上一页 next_cursor。跨用户历史与不存在的 Job 都返回 404；WORKER 角色不能使用用户查询。

页面使用 OpenAPI 生成的类型，服务端 fetch 禁用缓存和重定向、设置超时，并用固定中文消息处理异常。浏览器只接收任务标签和展示字段，不接收 USER token、runtime 网关配置或完整快照。刷新链接主动拉取最新状态；本批没有轮询、用户登录、取消按钮或轨迹 Viewer。

该目录是临时受控配置，不替代未来持久化 Registry 或用户资产授权。正式执行入口依然关闭；创建排队记录不等于已有运行中的 Worker。页面与数据库验收使用合成数据，不算新的真实模型验收。
