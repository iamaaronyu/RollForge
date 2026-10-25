# 开发进展与后续计划

更新日期：2026-10-26。依据已提交代码、专项验收记录及本轮多 Trial 开发；本文汇总当前事实和建议开发顺序，不把实验验收等同于生产开放。

## 当前结论

RollForge 已打通单 Trial 的真实执行与恢复闭环：鉴权 Hub → PostgreSQL 执行权 → Worker → 固定 Harbor/E2B → Claude Code → 受限 DeepSeek 会话 → 独立 Verifier → MinIO 原始产物 → Hub 终态。S0 在可信局域网、审核任务、并发 1 范围完成；S1 单 Trial 核心及第六个最小业务页面已实现；页面用合成数据完成真实浏览器/数据库往返，执行侧已有独立真实模型验收。

当前是可复现的实验执行底座。网页已支持受控任务创建、列表、评分、执行历史，以及 Registry 资产登记、版本浏览和基于私有 revision 的 Job 创建；Run 仍只能匹配部署者审核目录。Job 可创建 1–1000 个确定性 Trial 并分页查看。单进程有界持续调度、FIFO 队列和租约恢复已实现；真实多 Worker Harbor/E2B 链尚未复验。Registry API/UI、网关预算和多 Trial 验收工具已由真实 PostgreSQL、专项测试、类型检查和生产构建验证。2026-10-26 最新完整 `make check` 为 141 passed、48 skipped，前端类型检查和生产构建通过；`make runtime-check` 为 9 passed。跳过项包括未提供真实 PostgreSQL/MinIO 环境及历史 Harbor 轨迹样本，不计作真实存储或运行验收。Linux 主机可经用户指定的 Tailscale 地址访问，Ubuntu 22.04、6.8 内核、12 CPU、约 236 GiB 空闲磁盘；本轮只确认 E2B 主机基础环境，没有部署 RollForge 服务。当前可用内存约 2.5 GiB，维持并发 1。正式执行入口保持关闭。

## 已完成的开发项

| 模块 | 已交付内容 | 验证与边界 |
|---|---|---|
| 工程框架 | API/Web/Worker/Scheduler 分目录，共享 Python 包，独立 Runtime，依赖锁、配置示例、Make 命令与 CI | Scheduler 已实现单进程受限并发、FIFO 和过期回收；仍需真实双 Worker 和部署验收 |
| 开发与发布 | 中文项目文档、公开代码排除规则、每 4 小时正常 commit/push 自动检查 | 依赖桌面应用与电脑运行；不上传凭证、原始内部规划、日志或轨迹，不强推 |
| 固定运行组合 | Harbor 0.24.0、E2B 2.46.0、Claude Code 2.1.81、DeepSeek 官方 Anthropic Messages/deepseek-flash | 控制面与 Runtime 分开锁依赖；没有 Fork Harbor Core |
| 自托管 E2B | Linux/KVM、官方固定部署、SDK smoke、宿主预检与恢复脚本 | 约 8 GiB 测试机先验收并发 1；不承诺一般任务容量 |
| S0 真实执行 | 示例任务、原生结果解析、评分与零分、Agent/Verifier 故障、超时、重复取消清理 | 有真实模型/E2B 证据；零分与基础设施失败分开 |
| 凭证与验证隔离 | Mac 网关保留上游 Key，Sandbox 使用短期受限会话，独立 Verifier，网络负向探测与输出凭证扫描 | 会话限期、限次与输出限制已实现；SQLite 账本原子持久化请求次数。网关仍为 LAN HTTP，缺部署认证、token 级费用预算及生产密钥管理 |
| 部署恢复 | 服务重启、宿主重启后人工恢复隧道与防火墙，再次真实运行 | 验证的是人工恢复流程，无人值守恢复尚未实现 |
| Linux 服务化部署材料 | Hub loopback API、单并发 Scheduler 的 systemd 模板，分离 Hub/Worker/Runtime 私密配置，升级与本地 spool 恢复手册 | 模板语法已在目标机验证；RollForge 目录、凭证和服务尚未安装，未开放执行。详见 [Linux 部署记录](validation/2026-10-26-linux-deployment.md) |
| 领域契约 | Job/Trial/Execution、Revision 引用、不可变快照、状态机、租约与 fencing、RunnableBinding | Retry 创建新 Execution，保留逻辑 Trial/Attempt；快照不接受任意凭证字段 |
| PostgreSQL | 表、索引、约束、可回退迁移；创建、领取、续租、完成、有界过期回收 | 真实竞争、跳过锁、旧执行拒绝、零分与幂等、预算耗尽和迁移往返通过 |
| 鉴权 API/SDK | USER/WORKER 角色，Job 创建查询，Worker 领取续租完成，明确错误契约，Python SDK | 静态服务 token，不是用户登录/OIDC；Worker 仍为全局受信任身份 |
| API 类型 | OpenAPI 离线导出、生成 TypeScript 类型、一致性检查 | 前端消费已确认契约，不独立定义状态 |
| 对象存储 | S3 条件不可变写入、完整读回、Manifest 最后发布、摘要/作用域/原生评分核验 | 文件 64 MiB、执行 128 MiB、512 文件；没有 multipart、GC、STS 或生产租户授权 |
| 存储恢复 | 写入响应丢失、部分上传重放、MinIO 重启、上传进程 SIGKILL、缺失/篡改拒绝 | 8 项真实 MinIO 验收；发布完成后可脱离本地原始文件恢复，部分上传仍需本地文件 |
| Hub 联合恢复 | 实际提交后响应丢失，重建应用/SDK，租约过期且 Manifest 删除后幂等返回；旧产物无法绕过新执行权 | 真实 PostgreSQL/MinIO 与鉴权 API；应用重建不等同于宿主掉电 |
| Worker | 单次领取、续租、任务归档校验、凭证白名单、独立 Runtime、上传与 pending-commit、resume | 真实 HTTP 主链通过；修复 venv 解释器解析和 Mac 系统代理问题 |
| 真实 Worker 故障验收 | reward 1.0、0.0、提交响应丢失后恢复、协作式取消清理、同 Trial 新 Execution Retry、旧提交 409 | Sandbox/Firecracker 均为 0；协作取消不代表 Worker/Runtime SIGKILL 或掉电清理已通过 |
| 前端业务页 | 中文任务创建/列表/详情、服务端审核目录、Execution 历史与生成类型 | 合成数据浏览器验收通过；已有基础产物和主轨迹 Viewer；仍无登录、轮询 |
| 多 Trial Job | `trial_count` 1–1000、快照确定性展开、样本分页、Job 汇总计数及 Job 级 Execution 游标；页面可创建并展示样本 | 真实 PostgreSQL 创建、幂等、分页、领取、完成及 Retry 身份验收通过；多样本真实 Harbor/E2B/模型验收仍待完成 |
| 持续调度 | 有界常驻 Worker、空队列轮询、失败退避、优雅停机、FIFO 队列和租约自动回收 | PostgreSQL FIFO、双领取竞争、回收 API 与迁移通过；真实双 Worker Harbor/E2B、进程重启、全局容量限制仍待验收 |
| Registry 运行绑定与网页 | 用户可登记不可变 Task/Agent/Model 版本、浏览资产及最近 100 个 revision，并基于版本提交 Job；Hub 校验所有权、digest 和部署审核目录；直传可运行快照被拒绝 | 真实 PostgreSQL 的分页、所有权、摘要、审核不匹配及拒绝绕过用例通过；Next 类型检查和生产构建通过。真实多 Trial Harbor/E2B/模型验收仍待完成；网页仍是单用户静态 USER token 模式 |

此前各切片验收记录：调度器 `make check` 181 passed、2 skipped，`make runtime-check` 9 passed；Registry 绑定 `make check` 184 passed、2 skipped；Registry 页面 170 passed、16 skipped；加入多 Trial 验收工具时 `make check` 为 173 passed、16 skipped、1 项 Starlette/httpx 弃用警告。该轮 16 项跳过包括未提供固定 MinIO 测试二进制及历史 Harbor 轨迹样本；真实 PostgreSQL 迁移与 API 测试均运行通过。以往专用 MinIO 验收仍保留在对应历史记录中，不把跳过计作真实 MinIO 验收。当前最新完整检查结果见本文顶部。

证据入口：[S0 隔离与恢复](validation/2026-10-06-isolation-recovery.md)、[数据库](validation/2026-10-06-s1-postgres.md)、[API/SDK](validation/2026-10-06-s1-api.md)、[对象存储与联合恢复](validation/2026-10-06-s1-storage.md)、[真实 Worker](validation/2026-10-06-s1-worker.md)、[多 Trial 数据模型](validation/2026-10-26-multitrial.md)、[真实多 Trial 验收入口](validation/2026-10-26-multitrial-runner.md)、[FIFO 调度](validation/2026-10-26-scheduler.md)、[Registry 运行绑定](validation/2026-10-26-registry-binding.md)、[Registry 页面](validation/2026-10-26-registry-ui.md)、[网关持久预算](validation/2026-10-26-gateway-budget.md)、[Linux systemd 部署模板](validation/2026-10-26-linux-deployment.md)。早期验收文档保留当时尚未实现的历史边界；当前结论以上述最新记录和代码为准。

## 建议的后续开发顺序

### 0. 本轮环境收尾（已完成）

执行并复核 Linux 网络 restore：本轮 /32 放行与专属端口规则已撤销，原配置恢复，E2B 健康、无活动 Sandbox/Firecracker。核对私密会话撤销及临时服务清理，更新最新验收记录。

验收：提供恢复后的只读检查结果。维持正式入口关闭，不以新的前端或调度功能绕过运行环境条件。

### 1. 最小业务页面（本批已实现）

先为经审核的固定 Task/Agent/Model 提供单 Trial 创建页、Job 查询页、状态与评分/失败原因、Execution 历史。页面通过服务端调用 Hub，静态 token 不进浏览器或公开前端变量。创建只选择服务端已审核绑定，不接受自由输入网关 URL、对象键或环境变量。入口关闭时明确展示原因。

需补齐受控 Job 列表/执行历史查询 API、分页和访问控制，再从 OpenAPI 生成类型。查询不得把快照或配置中的内部接入信息直接当成页面展示内容。

验收：经授权用户可创建单任务 Job、查看运行/完成/失败与 Retry；越权查询拒绝，0 分显示为已评分；浏览器收不到服务 token。可用实验部署另行受控启用，不自动开放正式执行。

### 2. S2：结果与轨迹查看

实现版本化 Viewer 投影，保留原始结果；接通原始输出索引、文件列表、轨迹、工具调用和失败定位。缺失 usage/reasoning 显示未知，不能补造数值。增加受控产物读取 API或短期访问地址，避免浏览器获得 S3 管理凭证。

验收：页面能定位成功、零分、Agent/Verifier 异常的证据；拒绝跨用户和越界对象访问；大文件读取受限，旧 Manifest 仍可查看。对象 GC、multipart 仅在实际规模需要时引入。

### 3. S3 第一批：资产版本与确定性展开

增加经授权的 Task/Agent/Model/Dataset Registry 与不可变 Revision，记录来源、内容摘要和审核状态。把当前人工冻结的 Task 包变成受控注册流程；Job 依据 Revision 和 Attempt 确定性展开逻辑 Trial。保持 Trial/Attempt 与 Execution/Retry 分离。

验收：相同输入重放不会重复创建样本；修改任务生成新 Revision；未授权/未审核资产不可执行；Retry 不改变评测样本数量。先小批样本，再验证 100 个逻辑 Trial。

### 4. S3 第二批：持续 Worker 与容量调度（本地代码及 DB 验收完成，真实并发验收待完成）

将一次执行入口扩展为受控持续 Worker；调度器使用 PostgreSQL 权威队列，提供 FIFO、进程内资源容量、回收、退避和背压。已实现进程内并发槽位、空队列轮询、优雅停机与租约回收 API，并完成数据库和代码验收。Redis 如引入只做通知/缓存。明确停止接单、优雅退出、续租与回收的关系。

验收：数据库测试确认竞争领取唯一、过期回收、Retry fencing 与 FIFO；仍需两个真实 Worker 的 Harbor/E2B 执行、Worker/调度器重启与中断清理验收。当前主机先并发 1，资源确认后并发 2；100 个逻辑 Trial 不等于 100 并发。并发上限按单进程配置，多进程全局上限另行实现。

### 5. S4：运行可靠性与部署

在现有协作取消基础上增加 Job/Trial 取消意图和授权 API，完善 Worker/Runtime SIGKILL、宿主掉电、存储/数据库故障、模型限流与超时演练。Sandbox 的创建/删除仍由 Harbor 负责，平台恢复使用经过真实源码确认的扩展与流程。

提供服务化启动、健康/就绪依赖、备份恢复、升级/回退、结构化安全日志、租约与失败监控、异常告警和运行手册。给未完成上传定义保留期及安全 GC，避免删除活跃执行对象。

验收：故障后任务状态可追踪、重试有界、资源清理有证据；重启恢复可复现；数据备份实际读回；用户可从页面和监控定位故障。

### 正式开放前并行完成：网关与权限加固

把实验网关改为服务化 TLS 接入和部署认证，并定义 token 级费用预算、密钥轮换和撤销。请求次数预算已使用 SQLite 原子持久化；仍需明确 token 预算及失败/重试如何计费。补充用户会话登录、Worker 权限范围、对象存储最小权限和租户隔离；未知任务使用前需独立隔离验收。

验收：网关重启不能重置请求/费用预算，重复/并发请求不能超额；TLS 与部署认证到位；凭证不进入 Sandbox、日志或快照；跨用户/Worker/对象访问拒绝；节点网络策略不能扩大其他租户权限。正式开关需要这些条件及对应真实主链证据，不能只看网页可操作。

## 分工与交付流程

后续继续按切片交付：多 Trial 真实 Harbor/E2B 执行 → 双 Worker/中断恢复与容量边界 → 运行安全、部署和数据恢复。每片独立验证并记录证据；执行环境条件未满足时继续完成不依赖运行环境的功能，正式执行开关保持关闭。

每个切片顺序：明确不变量与验收 → 共享契约/迁移 → 服务与鉴权 → OpenAPI/消费者 → 真实数据库/存储验证 → 涉及主链则真实 Harbor/E2B/模型验收 → make check 与必要的 runtime-check → 凭证及公开适宜性审查 → 独立正常 commit/push → 更新证据。

Registry API 与版本发现/绑定页面已完成，真实 2-Trial 验收脚本已补齐并默认禁止执行。Linux systemd 模板和恢复手册已提交；目标主机当前没有 RollForge 安装目录、服务配置或已安装单元。下一项交付是先安全配置独立 Hub/PostgreSQL/对象存储与受限模型会话，再于 Linux 上执行并发 1 的真实多 Trial Harbor/E2B 运行与重启恢复验收；双 Worker/并发 2 需资源充足的主机。执行验收前需要有效的 Hub/Worker/S3 凭证与短期 DeepSeek 受限会话；本机目前仅有 `.env.spike` 的直接上游/E2B 参数，缺 Hub/Worker/S3 与受限会话配置。凭证应写入受保护本机/服务器文件，不经聊天传递。在此期间继续完成不依赖真实 Sandbox 的恢复和运维能力。实验比较、Failure Mining、训练导出、高级 Pause/Resume/Fork 和 RL 后置。工期按每个切片验收后再估算，当前测试主机容量与网关生产条件不能作为已具备的前提。

本批页面及查询验收见 [页面记录](validation/2026-10-06-s1-web.md)、[Registry 页面记录](validation/2026-10-26-registry-ui.md)；多 Trial 交付边界见 [多 Trial 验收记录](validation/2026-10-26-multitrial.md)。后续按 [逐步实施计划](next-implementation.md) 推进。

第二批已实现受控产物索引及 1 MiB 文本预览，真实 PostgreSQL/MinIO 权限、越界、篡改与预览边界通过；详见 [基础 Viewer 验收](validation/2026-10-06-viewer.md)。第三批主轨迹版本化投影与中文时间线已实现，159 项全量测试通过；详见 [时间线验收](validation/2026-10-06-trajectory.md)。多模态/子轨迹展开、搜索与聚合仍待开发。


2026-10-07 第四批：三类不可变资产版本注册表、迁移、USER API 与 SDK 已实现；注册表尚未接入执行快照或批量展开，不改变单 Trial 主链。数据库竞争、冻结、权限及带数据迁移回退通过，详见 [Registry 说明](registry.md) 和 [验收记录](validation/2026-10-07-registry.md)。下一切片为多 Trial Job 契约、迁移与确定性展开。
