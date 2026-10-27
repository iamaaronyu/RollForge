# Linux 任务状态自动刷新升级验收

日期：2026-10-27。目标节点：`100.106.244.122`。服务目录：`/opt/rollforge`。

## 升级内容

将任务列表和详情页的活动 Job 状态自动刷新部署到 `8cab986`。仅更新 Web 构建；Hub 未重启，未运行数据库迁移，没有修改服务环境文件、凭证、对象或执行开关。此前 Scheduler 已停止，升级前后均未启动。

升级前 checkout 为 `7099e66`，工作区干净；Hub readiness 与 Web 首页均返回 HTTP 200，控制面写入、Worker 执行和 Scheduler 开关均为 `false`。第一次 fetch 发现部署 checkout 的 `origin` 指向已删除的本机回退目录，因此通过用户提供的公开 GitHub URL 获取目标提交；升级成功后将 `origin` 修正为 `https://github.com/iamaaronyu/RollForge.git`。没有改写历史或处理冲突。

本机目标提交检查：`make check` 为 **240 passed、60 skipped**，Python lint/格式/OpenAPI 检查、Web API 类型检查和生产构建通过；`make runtime-check` 为 **9 passed**。跳过项依赖未配置的本机 PostgreSQL/MinIO fixture。

## 部署后核验

- `/opt/rollforge` 固定目录 checkout 为 `8cab986`，无未提交改动。
- `rollforge-web.service` 为 active，Web 首页 HTTP 200。
- `rollforge-hub.service` 保持 active，readiness HTTP 200。
- `rollforge-scheduler.service` 保持 inactive。
- 控制面写入、Worker 执行和 Scheduler 三个门仍为 `false`。

此节点已有 Job 均为终态，因此部署验收验证了生产构建、服务启动和 HTTP 可用性，没有在生产创建临时 Job 来触发浏览器自动刷新。刷新逻辑本身只在存在未结束样本时启用，页面不可见时暂停。
