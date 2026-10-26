# 后续实施计划与执行进度

日期：2026-10-26。按用户授权逐步实现，常规实现选择自主处理；缺少必要凭证或远端 root 操作时才请求用户。Linux 主机统一经 `100.106.244.122` 操作。阶段事实见 [进展汇总](progress-and-next-plan.md)。

| 顺序 | 本批交付 | 验收要求 | 当前状态 |
|---|---|---|---|
| 0 | Linux 临时网络恢复收尾 | 原配置恢复、临时规则为 0、E2B 健康、无 VM | 已复核；专项记录由另一 Agent 独立提交 |
| 1 | 用户范围 Job 列表与 Execution 历史 | 游标分页、越权拒绝、Retry 历史、无 Worker 身份泄露 | 已实现并通过本轮验收 |
| 2 | 中文单任务创建与查询页 | 服务端审核目录、token 不进浏览器、真实数据库页面往返 | 已实现并通过本轮验收 |
| 3 | 受控产物与轨迹 Viewer | 所有权、对象作用域、摘要与大小检查，失败定位 | 基础索引/文本、主轨迹时间线及浏览器内文本搜索已实现；ATIF 图片/音频元数据与有界内嵌子轨迹安全投影、Hub 验证后的 8 MiB 媒体预览已实现；大文件下载、外部子轨迹与聚合待开发 |
| 4 | 不可变 Revision Registry 与批量展开 | 版本冻结、幂等展开、Retry 不增加样本数 | API、SDK、资产发现分页及中文 Registry 页已实现；最新版本可选创建审核范围内 Job；真实 PostgreSQL 所有权/摘要/拒绝与分页测试通过。新增真实多 Trial 验收入口；仍待具备运行凭证后完成 Harbor/E2B/模型运行，见 [验收入口说明](multitrial-acceptance.md)、[绑定验收](validation/2026-10-26-registry-binding.md) 与 [页面验收](validation/2026-10-26-registry-ui.md) |
| 5 | 持续 Worker 与容量调度 | 真双 Worker、背压、重启恢复、有界回收 | 有界常驻 Worker、FIFO 入队、退避、优雅停机与鉴权租约回收已实现；18 项真实 PostgreSQL 执行/API 测试通过；双 Worker Harbor/E2B、进程重启和全局容量验收待完成，见 [验收记录](validation/2026-10-26-scheduler.md) |
| 6 | 部署与故障演练 | SIGKILL/掉电、限流、备份读回、监控与升级恢复 | 新增独立服务端口、loopback Web/Hub、强凭证与隔离预检、安全数据服务启动器及跨组件备份/恢复工具；SQLite 真账本备份、单调合并与 bundle 摘要测试通过。真实 PostgreSQL/MinIO 临时恢复读回尚未验证（本机 Docker daemon 不可用），目标机部署仍需管理员操作；真实 Sandbox 取消清理、监控待完成。正式写入/执行开关保持关闭 |
| 并行前置 | 网关与权限加固 | TLS/部署认证、token 级费用预算、用户登录、Worker/对象权限 | 请求次数已由 SQLite 持久化原子扣减；其余正式开放前完成 |

每一切片先共享契约与权限，再实现 API/生成类型/页面；相关不变量用真实 PostgreSQL/MinIO 验证。修改执行链时补真实 Harbor/E2B/模型证据。提交前 make check；Runtime 改动追加 runtime-check。按文件独立提交，不混入其他 Agent 的改动。

本批页面是本机单用户实验界面：服务端配置单一 USER token，不是多用户登录。默认只读，缺少配置或未显式允许实验时不创建任务。审核目录只在 Hub 服务端加载，浏览器不能设置网关、对象键或快照。任务进入 PostgreSQL 队列后仍需独立 Worker；不启动模型或改变正式执行开关。

2026-10-26：新增 Job 多 Trial 样本数、确定性展开、列表分页和摘要；Execution 历史游标改为 Job 内单调序号，以容纳不同样本交错重试。数据库迁移安全回填既有单样本 Job，并在降级会丢失样本时拒绝回退。代码和 DB 验收完成；多 Trial Harbor/E2B/模型主链与 Registry 运行绑定是本阶段剩余关卡。

2026-10-26：开始 S1 调度切片，增加有界持续 Worker、指数退避、SIGTERM 清理和 Worker 鉴权租约回收 API；队列按入队时间、Job ID、样本序号排序，重试重新入队到队尾。进程容量为本地上限，不代表多进程集群额度。真实双 Worker Harbor/E2B 和中断恢复仍需验证。

2026-10-26：增加 `/jobs/from-revisions`，把用户拥有的 Task/Agent/Model revision 安全绑定至部署者审核快照，并在执行快照中保留注册引用。普通 `/jobs` 拒绝带 `runtime` 的请求，防止用户绕过审核。Registry 网页和资产发现分页已完成；真实多 Trial Harbor/E2B/模型验收仍待完成。

2026-10-26：增加 USER Job/Trial 取消接口和 SDK、详情页确认按钮及 `CANCELLED` Execution 状态。取消与完成事务使用同一 Trial 锁；真实 PostgreSQL 幂等、权限、租约撤销、并发完成互斥和迁移回退/重放通过。真实 E2B 清理时延与 SIGKILL/掉电恢复仍待验收。
