# 后续实施计划与执行进度

日期：2026-10-27。按用户授权逐步实现，常规实现选择自主处理；缺少必要凭证或远端 root 操作时才请求用户。Linux 主机统一经 `100.106.244.122` 操作。阶段事实见 [进展汇总](progress-and-next-plan.md)。

| 顺序 | 本批交付 | 验收要求 | 当前状态 |
|---|---|---|---|
| 0 | Linux 临时网络恢复收尾 | 原配置恢复、临时规则为 0、E2B 健康、无 VM | 已复核；本轮再次尝试的 Sandbox placement 未成功，临时 override/规则已清理，恢复后 SDK 创建/销毁 smoke 通过；见部署审计 |
| 1 | 用户范围 Job 列表与 Execution 历史 | 游标分页、越权拒绝、Retry 历史、无 Worker 身份泄露 | 已实现并通过本轮验收 |
| 2 | 中文单任务创建与查询页 | 服务端审核目录、token 不进浏览器、真实数据库页面往返 | 已实现并通过本轮验收 |
| 3 | 受控产物与轨迹 Viewer | 所有权、对象作用域、摘要与大小检查，失败定位 | 基础索引/文本、主轨迹时间线及浏览器内文本搜索、ATIF 多模态与内嵌子轨迹、受控外部 JSON 子轨迹按需读取、Hub 验证后的媒体预览/文件下载及 Job 评分统计已实现；外部子轨迹每次按需读取并限制 1 MiB，不自动遍历引用；详见 [外部子轨迹验收](validation/2026-10-27-external-subtrajectory.md) |
| 4 | 不可变 Revision Registry 与批量展开 | 版本冻结、幂等展开、Retry 不增加样本数 | API、SDK、资产发现分页及中文 Registry 页已实现；真实 PostgreSQL 所有权/摘要/拒绝与分页测试通过。2026-10-27 Registry 绑定的真实双 Trial Job 已 2/2 完成（并发 1）；待扩展到多进程 Worker、更多样本与故障恢复，见[部署审计](validation/2026-10-26-deployment-audit.md) |
| 5 | 持续 Worker 与容量调度 | 真双 Worker、背压、重启恢复、有界回收 | 有界常驻 Worker、FIFO 入队、退避、优雅停机与鉴权租约回收已实现；18 项真实 PostgreSQL 执行/API 测试通过；双 Worker Harbor/E2B、进程重启和全局容量验收待完成，见 [验收记录](validation/2026-10-26-scheduler.md) |
| 6 | 部署与故障演练 | SIGKILL/掉电、限流、备份读回、监控与升级恢复 | Linux Hub/Web、PostgreSQL、Silo 对象存储已部署；独立端口、权限、Hub readiness、S3 角色隔离及数据容器重启读回已有现场证据。Hub/Web active，Scheduler inactive，写入/执行门全关。Mac 受限网关 LaunchAgent 已运行、会话为空，启动即创建的 SQLite 预算账本现已在 0600 权限下实机验证。PostgreSQL 容器内客户端的只读指纹、`pg_dump` stdout 和 `pg_restore` stdin 路径已实机验证；加密备份卷、独立只读 S3 账号及真实创建/恢复仍未就绪。E2B 持久 `/32` 配置与单端口防火墙单元已实现并推送，但尚未在节点启用，需确认此设置作用于该节点所有 Sandbox 且仅用于独占 RollForge 节点。真实双 Worker/取消、宿主故障及监控验收仍待完成 |
| 并行前置 | 网关与权限加固 | TLS/部署认证、真实供应商费用对账、用户登录、Worker/对象权限 | 会话限时/限次/费用上限、单会话及全部撤销命令已实现并通过合成配置并发/权限测试；真实签发/撤销运行验收、供应商账单对账、TLS 和部署身份认证仍待验收 |

每一切片先共享契约与权限，再实现 API/生成类型/页面；相关不变量用真实 PostgreSQL/MinIO 验证。修改执行链时补真实 Harbor/E2B/模型证据。提交前 make check；Runtime 改动追加 runtime-check。按文件独立提交，不混入其他 Agent 的改动。

本批页面是本机单用户实验界面：服务端配置单一 USER token，不是多用户登录。默认只读，缺少配置或未显式允许实验时不创建任务。审核目录只在 Hub 服务端加载，浏览器不能设置网关、对象键或快照。任务进入 PostgreSQL 队列后仍需独立 Worker；不启动模型或改变正式执行开关。

2026-10-26：新增 Job 多 Trial 样本数、确定性展开、列表分页和摘要；Execution 历史游标改为 Job 内单调序号，以容纳不同样本交错重试。数据库迁移安全回填既有单样本 Job，并在降级会丢失样本时拒绝回退。代码和 DB 验收完成；真实 Registry 绑定双 Trial 主链于 2026-10-27 完成 2/2。双 Worker、持续网关、更多样本和中断恢复仍待验收。

2026-10-26：开始 S1 调度切片，增加有界持续 Worker、指数退避、SIGTERM 清理和 Worker 鉴权租约回收 API；队列按入队时间、Job ID、样本序号排序，重试重新入队到队尾。进程容量为本地上限，不代表多进程集群额度。真实双 Worker Harbor/E2B 和中断恢复仍需验证。

2026-10-26：增加 `/jobs/from-revisions`，把用户拥有的 Task/Agent/Model revision 安全绑定至部署者审核快照，并在执行快照中保留注册引用。普通 `/jobs` 拒绝带 `runtime` 的请求，防止用户绕过审核。Registry 网页和资产发现分页已完成；真实双 Trial 绑定运行证据见 2026-10-27 部署审计。

2026-10-26：增加 USER Job/Trial 取消接口和 SDK、详情页确认按钮及 `CANCELLED` Execution 状态。取消与完成事务使用同一 Trial 锁；真实 PostgreSQL 幂等、权限、租约撤销、并发完成互斥和迁移回退/重放通过。真实 E2B 清理时延与 SIGKILL/掉电恢复仍待验收。

2026-10-27：远端真实 PostgreSQL 全集成测试发现已完成 Job 的取消请求可能无变更但仍返回成功，修复为终态冲突并保留已取消幂等和多 Trial 部分取消；`a613c05` 已部署，38 项 PostgreSQL 集成测试通过、15 项真实 MinIO/轨迹环境相关测试跳过。

2026-10-27：Viewer 新增受控外部 JSON 子轨迹按需读取，`b9476b2` 已提交并推送；临时 PostgreSQL 16 + 真实 Silo S3 API 验收通过。包含该功能的 `d209366` 已部署到 Linux systemd，Hub readiness 与 Web 首页返回 200，Scheduler 与写入/执行门保持关闭。详见[升级验收记录](validation/2026-10-27-linux-upgrade.md)。
