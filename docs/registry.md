# 不可变资产版本注册表

当前交付 Task、Agent、Model 的用户私有版本注册表、PostgreSQL 迁移、鉴权 API 与 Python SDK。注册仅保存白名单配置，不创建 Job、不启动 Worker、不授予任务执行许可；既有审核目录继续独立生效。

## 契约与冻结

共享定义在 packages/schemas/registry.py。调用方提供 asset_id（UUID）和 revision（1 至 1000000），服务端从认证身份确定 owner_id，计算规范化 JSON 的 SHA256 配置摘要。资产首次创建固定所有者和类型；同一资产版本从 1 连续增加。相同版本和内容重复提交返回同一记录；修改已保存内容、改变类型或跳过版本号返回 409。并发创建在 PostgreSQL 事务中锁定资产行，唯一键处理初次创建竞争。

TaskSpec 仅包含对象存储 archive_key 和 archive_digest；AgentSpec 固定 Claude Code 2.1.81；ModelSpec 固定 deepseek-flash、anthropic-messages 与 gateway-session，只允许无凭证、无路径、无查询串的网关地址。不接受 api_key、任意环境变量或任意扩展配置；错误不回显输入。白名单不能识别故意藏在合法文本中的秘密，部署者仍需保证提交配置适合持久化与展示。

注册 digest 表示整个规范化 spec，不等于任务压缩包摘要或既有 RunnableBinding 的 Agent/Model 摘要。它们分别保存，未来解析成执行快照时需分别核对。注册不会读取压缩包或校验网关连通性，也不证明未知任务已通过隔离验收。

数据库 0002_registry 增加 registry_assets、registry_revisions，固定资产所有权/类型，复合主键固定资产与版本。数据库触发器阻止 UPDATE、DELETE 和 TRUNCATE；应用未提供修改/删除接口。数据库管理员禁用触发器或直接修改权限不属于此保护范围。

## API 与 SDK

所有接口要求 USER 权限；写接口另需控制面写开关。Worker 拒绝访问。跨用户与不存在的资产均为 404。

| 接口 | 用途 |
|---|---|
| POST /api/v1/registry/revisions | 提交 RevisionCreate，返回 AssetRevision |
| GET /api/v1/registry/assets/{asset_id}/revisions/{revision} | 读取固定版本 |
| GET /api/v1/registry/assets/{asset_id}/revisions?limit=20&after=0 | 按版本递增分页 |

limit 为 1–100，after 为非负版本游标；next_cursor 为 null 时结束。SDK 的 create_revision、get_revision、list_revisions 对应以上接口。OpenAPI 与前端生成类型已更新；本批尚无 Registry 网页、资产发现列表或批量展开接口。

## 迁移与接续

按现有 Alembic 流程 upgrade head。downgrade 0001_execution 会移除注册表及其数据，保留 Job/Trial/Execution；运行环境回退前必须先备份注册数据。真实专用数据库已验证回退、重新升级与元数据一致性，不自动迁移远端长期运行数据库。

下一切片：设计多 Trial Job 的冻结输入与确定性展开契约，迁移单 Trial 限制，同步查询、执行快照解析与 Worker 领取语义。Attempt 表示独立样本，Retry 仍只产生新 Execution；不能把重复执行当作新增样本。此项涉及执行主链，启用前必须追加真实 Harbor/E2B/模型验收。
