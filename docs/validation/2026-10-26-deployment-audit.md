# 2026-10-26 当前部署独立测试

## 结论与范围

目标节点 `100.106.244.122` 当前控制面部署通过本轮健康、真实 HTTP 鉴权、关闭开关、对象存储权限、页面读取和服务/数据容器重启测试。**尚未通过真实任务执行及完整上线就绪验收。** Hub/Web 运行，Scheduler inactive；任务写入、Worker 执行和 Web 实验开关保持关闭。

服务器 Git checkout 为 `4a9f2fb`，本地验收时 HEAD 为 `22d8855`。当前部署不包含后续预算恢复回归等提交，不能把本地最新检查视作远端已升级。没有因验收更新服务器代码、开启任务执行或请求模型；没有重启宿主或 E2B 服务。

## 现场验证结果

本轮现场脚本共 94 项断言通过、0 失败，包含 HTTP 状态/错误契约、响应凭证扫描、持久性及临时资源清理断言；这是检查项数量，不代表 94 种独立业务场景。

| 检查 | 实际结果 |
|---|---|
| systemd 与权限 | Hub/Web active，无自动重启；以 rollforge 用户运行，NoNewPrivileges、ProtectHome、ProtectSystem=strict 生效。infra.env 为 root 0600，其余服务配置 root:rollforge 0640，Runtime 文件 rollforge 0600 |
| 监听与访问边界 | Hub 8000、Web 3300、PostgreSQL 55432、Redis 56379、S3 59000/59001 均绑定 loopback；从节点 Tailscale 非 loopback 地址连接这些端口均被拒绝 |
| HTTP 健康 | live、ready 和 platform 返回 200；ready 仅证明 PostgreSQL 连通，S3/Redis 通过另行请求验证 |
| HTTP 鉴权 | 缺失/无效 Token 为 401；Worker 读取用户 Job/Registry 为 403，USER 领取租约为 403；有效 USER 可读取 Job、审核目录和资产列表 |
| HTTP 关闭开关与参数 | 创建、取消、领取、过期回收均被 WRITES_DISABLED/503 拦截；不存在 Job/Revision 为 404，非法 UUID/分页为 422；OpenAPI 与服务器 checkout 文档一致 |
| Web | 首页、任务页、Registry 页及静态资源均为 200；不存在 Job 为 404。通过真实浏览器确认任务/Registry 为空且创建/登记按钮禁用，未提交表单 |
| 凭证边界 | 页面 HTML、9 份已引用 JS/CSS 资源、测试 API 响应及 Hub/Web 当日日志中，未发现实际服务配置/Runtime 中的 Token、存储密钥或密码值；页面未暴露配置的模型网关地址。Runtime 未包含平台存储/Hub/直接上游 Key 字段。这是本次采集范围内的检查，不代表所有历史文件均已扫描 |
| PostgreSQL 运行库 | 只读事务核对迁移为 0005_cancelled_executions；Job、Trial、Execution、Registry Asset/Revision 均为 0，无运行中 Execution |
| S3 真实权限 | Worker 上传及读回、Hub 读回成功；Hub 写/删、Worker 删及二者列出所有 bucket 被拒；匿名读被拒；IfNoneMatch 冲突为 412，原字节不变 |
| Redis | PING、带 TTL 的随机测试 Key 写入/读回通过，测试 Key 已删除；Redis 未充当任务权威状态源 |
| Hub/Web 重启 | 分别通过 systemd restart 更换进程后恢复 readiness/任务页 200，Scheduler 保持 inactive |
| 数据持久性 | 专用临时数据库写入已提交合成行，随机 S3 Key 上传公开合成字节；仅重启 RollForge PostgreSQL/Silo 容器后，两者读回一致，Hub readiness 恢复 200 |
| 最终清理 | 两个本轮专用临时数据库、两枚 S3 测试对象、Redis Key、临时浏览器及 SSH 隧道均已清理；Hub/Web、RollForge 数据服务及原 E2B 服务健康，Firecracker 为 0 |

现场脚本和脱敏逐项结果保留在本机忽略目录 `outputs/deployment-audit-20261026/`，不提交运行产物或私密配置。S3 测试只操作本轮随机前缀对象；使用已有管理员身份清理本轮测试对象，没有修改应用身份策略或权限。

## 代码与真实数据库验证

在服务器实际部署 checkout 上，用现有 PostgreSQL 服务创建独立随机测试数据库后运行完整 pytest：**204 passed、16 skipped、1 warning**，退出码 0。测试数据库已删除，没有在运行库创建测试 schema。

这包含真实 PostgreSQL 下的状态机、租约竞争/fencing、取消、分页、Registry 所有权与迁移测试；其中 API 业务测试使用 ASGI 测试传输，不能冒充真实 Worker/Harbor/E2B 全链。16 项跳过需要固定 MinIO 测试二进制或历史 Harbor 原始轨迹样本；本轮现场 S3 请求与重启读回通过，不等于全部历史对象存储专项再次运行。

服务器固定 Runtime 契约检查：**9 passed**，无 Sandbox/模型调用。本地 `make check`：**170 passed、51 skipped、1 warning**，lint/格式、OpenAPI、TypeScript 和生产构建通过。51 项跳过未提供本地真实数据库/MinIO/轨迹环境，不能计入真实验收。首次本地检查受沙箱 loopback 绑定限制失败；在允许本机回环端口的环境重跑通过，没有修改测试规避失败。

## 尚未验收与明确阻塞

1. **备份及隔离恢复未通过**：`/mnt/rollforge-backups` 不是挂载点，无法确认加密备份位置；目标宿主未提供 `pg_dump`、`pg_restore`、`psql`，现有备份工具依赖这些宿主客户端。没有生成明文运行库备份或用运行库做恢复目标。容器重启保留不等于备份读回、宿主掉电或灾备恢复。
2. **真实任务闭环未运行**：部署中的 Job、Registry 资产/Revision 为空，现有记录确认审核 Task 归档也未准备。没有为通过测试而注册任意任务、打开执行门或消耗新的模型请求。需要先准备并审核输入，再按明确会话/预算进行受控单 Trial、多 Trial、取消及中断恢复验收。
3. **网关线上恢复未验收**：已有单次上游 200 与临时 SQLite 回归不能替代线上预算账本备份/恢复、会话过期/撤销、TLS/部署认证及费用预算验收。本轮未重启网关或更改会话。
4. **整机恢复与容量未验收**：本轮未执行宿主重启/掉电、Worker/Runtime SIGKILL、双 Worker 或并发 2。主机约 8 GiB 内存，有 Swap 使用，仍保持并发 1。
5. **生产用户与数据边界未验收**：当前是 loopback/SSH 隧道单用户静态 Token 页面，无多用户登录；缺少实际产物与轨迹，线上详情/Viewer 不能完成带数据浏览器验收。正式开放仍依赖既定权限、网关与恢复关卡。

后续应先补齐备份设施/客户端及审核输入，再进行隔离恢复与受控真实执行；本记录不批准开启正式执行入口。

## 2026-10-27 更新部署与远端数据库回归

部署已按 GitHub `main` 快进到 `a613c05`；升级前远端代码引用 `4a9f2fb` 已保存为仅本机部署回退引用。升级仅包含先前 Viewer/轨迹文件读取和取消 API 竞争修复，没有数据库迁移或依赖锁变化。Next.js 生产构建通过；Hub/Web 重启后 health/readiness 与 Web 首页均为 HTTP 200，Scheduler inactive，Hub 写入、Worker 执行、Scheduler 和 Web 实验开关均为 false。Git 工作区清洁。配置文件名、变量状态和权限已只读检查，未输出任何私密值。

固定 Harbor/E2B Runtime 离线契约测试在远端为 9 passed；Worker 与 Scheduler import-only 检查通过。没有创建 Sandbox 或调用模型。

远端 PostgreSQL 完整集成测试在随机隔离 schema 中运行，测试结束自动删除 schema：修复前 36 passed、15 skipped、1 failed，失败定位到“完成先提交后，取消已完成 Job 仍返回成功”的终态语义问题。修复 `a613c05` 部署后重跑为 **38 passed、15 skipped、0 failed**。15 项跳过包括需要固定 MinIO 测试二进制的 Silo 对象存储用例及缺少历史 Harbor 轨迹样本的用例。测试不修改既有业务表；运行库的 Job、Trial、Execution、Registry Asset/Revision 仍均为 0，迁移版本为 `0005_cancelled_executions`。

状态复核仍未满足真实多 Trial 前置条件：目标机没有 Registry revision、获批 Task archive 或部署审批绑定；上一次临时模型网关会话/网络规则已恢复原状，常驻网关路径和当前会话有效性未验收。`/mnt/rollforge-backups` 仍非加密挂载卷，宿主 PG 备份客户端未安装，因此没有可用的加密备份/隔离恢复证据。正式执行门保持关闭；不得把本轮控制面部署和数据库竞争测试计作真实 Worker→Harbor/E2B→模型多 Trial 验收。

## 2026-10-27 后续部署准备复核

通过 `tyu@100.106.244.122` 只读复核：Hub/Web active，Scheduler inactive/disabled，四个写入/执行开关仍为 false。Runtime 私密文件仅含 E2B 凭证、模型会话 Token 和 E2B endpoint 变量名，权限为 `rollforge:rollforge 0600`；未读取或输出任何值。PostgreSQL 中 Job、Registry Asset、Registry Revision 与 Execution 均为 0。

公开 `hello-task` 六文件归档已使用 Worker 对象存储身份上传至 Silo 并读回校验，归档摘要为 `sha256:01481611aa8ba77d86961a78783ac46b769a35ea1094988cbf123a7b19d56345`。它尚未登记为 Registry Revision，也没有部署审批绑定；没有创建 Job 或改变执行开关。

备份准备仍未就绪：`/mnt/rollforge-backups` 不是独立挂载点，当前位于根 ext4 文件系统；宿主没有 `pg_dump`、`pg_restore`、`psql`。尝试刷新 APT 索引时 Ubuntu 镜像返回 HTTP 403，索引更新失败且未安装软件。数据库运行在 PostgreSQL 16 容器内。后续需先修复受信任的 Ubuntu 包镜像并安装匹配的 PostgreSQL 16 客户端，或实现并验证容器化客户端流程；还需配置独立加密备份介质及恢复目标。不能以根盘上的普通目录代替加密备份卷。

本地网关复核中，工作区沙箱阻止访问本机 Tailscale 偏好与进程列表；DeepSeek Key 文件存在且权限为 0600，但未读取或输出其值。当前没有证据证明网关常驻可达或 Runtime 会话仍有效。服务器端没有网关部署/预算数据库证据。网关连通、会话有效/过期/撤销、预算账本重启与恢复均未验收，因此不能登记批准执行配置或开启 Worker/Scheduler。

## 2026-10-27 真实双 Trial 闭环与收尾

### 环境修复与验收

服务器 Runtime venv 最初只有 Python 解释器，缺少固定 Harbor/E2B 依赖。使用锁定文件运行 `uv sync --project integration/harbor-runtime --python 3.12 --locked` 后，远端 `make runtime-check` 为 **9 passed**。首个 Worker 尝试又确认公开 `hello-task` 未声明独立 Verifier；Worker 按安全契约拒绝该任务，两个 Trial 以失败/过期终止，未调用模型。样例现已在 `[verifier]` 明确设置 `environment_mode = "separate"`，修复提交 `142464e` 已推送。

新归档使用 Worker 对象存储身份上传并读回校验；Worker 解包摘要匹配，固定 Harbor 解析出的 Verifier 模式为 `separate`。该版本绑定为 Task Revision 2。临时模型网关仅允许 `deepseek-flash`、单次最多 8192 输出 token；本次签发的 1 小时会话最多 12 个请求。

随后通过 Registry 绑定创建的真实双 Trial Job 完成 **2/2**：两个 Trial 均为 `COMPLETED`、奖励均为 **1.0**，生成 **2 个不同 Execution 和 2 个不同 Manifest**，验收器确认 Registry 选择与 Job 快照一致。真实链路覆盖 Hub、PostgreSQL、Silo、Scheduler、Worker、Harbor 0.24.0、E2B 2.46.0 和 DeepSeek 网关。Worker/Scheduler 并发为 1。

### 收尾状态

验收后 E2B Sandbox 数与 Firecracker 进程数均为 0。短时模型会话先被网关撤销，旧 Token 请求得到 403，随后从服务器 Runtime 文件删除；本机网关与负向端口探测器均已停止。E2B 的单地址临时 allowlist 和 `veth+` TCP 11441 防火墙规则已全部移除，Orchestrator 恢复原 Compose 配置并保持 healthy。

Hub/Web readiness 与首页均为 HTTP 200；Scheduler inactive/disabled。Hub 控制面写入、Worker 执行、Scheduler 和 Web 实验开关均为 false。数据库保留成功与失败验收记录；Registry 有 Task/Agent/Model 三类资产和四个版本。成功结果可由 Hub 查询，未把原始轨迹、响应或凭证提交到仓库。

本地 `make check`：**185 passed、52 skipped、1 warning**；本地与服务器 Runtime 检查均为 **9 passed**。GitHub `main` 已包含样例修复提交 `142464e`。

### 正式开放前仍需完成

1. **持久化网关和 E2B 网络规则**：本次 Mac 网关、E2B `/32` allowlist 与端口防火墙只用于受控验收，随后已恢复/停止；服务器 Runtime 当前没有模型会话 Token。需建立可恢复的常驻网关、持久但只允许网关单地址 TCP 11441 的 Sandbox 规则，并再验收重启、过期和撤销。当前网关为受限 LAN HTTP 实验配置，生产 HTTPS/部署认证尚未完成。
2. **加密备份和隔离恢复**：`/mnt/rollforge-backups` 仍不是独立加密挂载卷，宿主缺少 PostgreSQL 16 备份客户端；本次 APT 更新受 Ubuntu 镜像 HTTP 403 阻塞，未安装软件。需准备至少 20 GiB、建议 60 GiB 的独立加密备份卷，修复可信包源或验证容器化 PostgreSQL 客户端路径，然后按 [跨组件备份与恢复手册](../backup-recovery.md) 完成真实备份读回和隔离恢复。
3. **恢复与容量关卡**：整机重启、Worker/Scheduler 强制中断后的恢复，以及 8 GiB 主机上的资源/费用上限仍待单独验收。

因此，本轮证明了真实平台双 Trial 功能闭环，但不批准打开常驻执行入口；上述运行与灾备关卡通过前，所有执行门保持关闭。
