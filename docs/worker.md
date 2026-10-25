# 单 Trial Worker

Worker 支持单次领取，也可由持续调度进程以有限并发反复调用。默认关闭；仅面向已审核 Task、已批准的受限模型网关和受控实验环境。真实 Worker → Harbor → E2B → 模型 → 对象存储 → Hub 单 Trial 全链已在受限 LAN、并发 1 的合成样例范围通过。持续调度代码与真实 PostgreSQL FIFO/租约回收测试已实现；多 Worker 真实 E2B 竞争尚未验收。实验网关现有跨进程/重启持久化的请求次数预算，但尚无 TLS、部署身份认证或 token 级费用预算；`platform.execution_enabled` 仍保持 false。

## 配置与凭证

控制面使用仓库根目录锁定依赖；独立 Runtime 使用 `integration/harbor-runtime/uv.lock` 的 Harbor 0.24.0、E2B 2.46.0。先执行 `make install runtime-install`。

以下为配置名称，实际凭证只放入受保护的本地配置或凭证系统，不填写到仓库示例：

| 配置 | 用途 |
|---|---|
| `ROLLFORGE_WORKER_RUNS_ENABLED=true` | 显式允许 Worker 执行或恢复 |
| `ROLLFORGE_WORKER_HUB_URL`、`ROLLFORGE_WORKER_HUB_TOKEN` | Hub 地址和 WORKER 角色 token |
| `ROLLFORGE_WORKER_RUNTIME_PYTHON` | 独立 Runtime Python 的绝对路径 |
| `ROLLFORGE_WORKER_RUNTIME_ENV_FILE` | 私密 Runtime 会话文件的绝对路径 |
| `ROLLFORGE_WORKER_GATEWAY_URL` | 与快照完全匹配的已批准网关根地址 |
| `ROLLFORGE_WORKER_ALLOW_LAN_HTTP` | 默认为 false；受限 LAN 实验才显式启用 |
| `ROLLFORGE_WORKER_SPOOL` | 本地受保护恢复目录，默认 outputs/worker |
| `ROLLFORGE_OBJECT_STORAGE_ENDPOINT`、`ROLLFORGE_OBJECT_STORAGE_BUCKET` | Worker 与 Hub 使用同一存储 |
| `ROLLFORGE_OBJECT_STORAGE_ACCESS_KEY`、`ROLLFORGE_OBJECT_STORAGE_SECRET_KEY` | 平台对象存储凭证 |
| `ROLLFORGE_OBJECT_STORAGE_ALLOW_LOCAL_HTTP` | 默认为 false；仅允许显式本机 HTTP 测试 |
| `ROLLFORGE_SCHEDULER_ENABLED` | 默认 false；常驻调度进程的显式开关 |
| `ROLLFORGE_SCHEDULER_CONCURRENCY` | 每个进程 1–16 个 Worker 槽位，默认 1 |
| `ROLLFORGE_SCHEDULER_POLL_SECONDS` | 空队列轮询间隔，0.1–30 秒 |
| `ROLLFORGE_SCHEDULER_RECOVERY_SECONDS` | 过期租约回收间隔，1–300 秒 |
| `ROLLFORGE_SCHEDULER_SHUTDOWN_SECONDS` | 停止接单后等待当前执行的宽限时间，1–3600 秒，默认 300 秒 |

Runtime 会话文件必须是普通文件，权限 0600、直接父目录 0700；包含 `ROLLFORGE_MODEL_SESSION_TOKEN` 和 `E2B_API_KEY`，以及自托管 `E2B_API_URL` / `E2B_SANDBOX_URL` 或 `E2B_DOMAIN`。会话须由已批准的网关分配并限制预算、期限和权限。Worker 不自动创建会话，不接受上游 `ANTHROPIC_API_KEY` 代替受限会话，不展开文件中的环境变量引用。

子进程只接收受限会话、E2B 配置、PATH、独立 HOME、LANG、固定输出限制和固定代理隔离配置；Hub/S3 凭证不传入。配置的 Runtime Python 使用绝对路径并保留 venv 符号链接，不能 resolve 到基础解释器；显式清空代理变量，NO_PROXY 列出可信端点而非通配符，避免固定 SDK 采用宿主代理。网关默认要求 HTTPS，URL 不能含凭证、路径或查询参数。原始结果在任何上传前扫描已知平台/会话凭证，发现泄露则停止上传；该扫描不能保证识别任务自行获得的所有未知秘密。

## 冻结任务与运行绑定

先审核 Task，显式配置 `[verifier] environment_mode = "separate"`，按 S0 验收的任务和网络限制运行。仅设置 separate 不意味着未知 Task 自动具备已验收的隔离策略。

使用 `rollforge_worker.tasks.pack_task(task_dir)` 获得 tar.gz 字节和规范内容摘要，再通过 `S3ObjectStore.put_immutable` 上传到不可变 Task 对象键，例如 `tasks/<归档摘要>.tar.gz`。内容摘要包含普通文件路径、字节摘要、大小和可执行语义，不依赖主机绝对路径。Worker 拒绝符号链接、特殊文件、越界路径、重复文件和超预算归档，并在解包后重新核对内容摘要。

`ExecutionSnapshot.runtime` 使用 `packages/schemas/runnable.py` 的 `RunnableBinding`：Task 归档键和摘要、固定 Claude Code 2.1.81、Anthropic Messages、deepseek-flash、网关地址、gateway-session 凭证模式和 separate verifier。`snapshot.task.digest` 使用规范内容摘要；`snapshot.agent.digest` 和 `snapshot.model.digest` 分别使用 binding 的 `agent_digest`、`model_digest`，不手工发明版本摘要。Revision ID 和版本由已有资产记录/调用者提供，当前尚未实现 Registry 授权校验。

通过 USER 角色 SDK 创建 Job。Worker 请求 `runnable_only=true`，跳过没有运行绑定的元数据 Job。租约包含服务端确认的 job_id、trial_id、execution_id、worker_id 和 fencing_token。

## 执行与恢复

```sh
make worker-check
# 必须另行设置上述配置，不能仅运行命令就开放执行。
UV_CACHE_DIR=.cache/uv uv run python -m rollforge_worker --run
# 仅恢复指定已有 Execution 的上传或数据库提交，不重跑模型。
UV_CACHE_DIR=.cache/uv uv run python -m rollforge_worker --run --resume /absolute/private/spool/execution-id
# 常驻调度；仅在私密服务配置显式启用 scheduler 和 worker 执行后运行。
UV_CACHE_DIR=.cache/uv uv run python -m rollforge_scheduler --run
```

调度器须同时显式启用 `ROLLFORGE_SCHEDULER_ENABLED` 与 `ROLLFORGE_WORKER_RUNS_ENABLED`。它用 `ROLLFORGE_SCHEDULER_CONCURRENCY` 或 `--concurrency` 限制单进程 Worker 槽位（1–16，默认 1）；空队列按配置轮询，失败指数退避，恢复循环调用鉴权租约回收 API。停止时先停止新领取并等待现有执行；超过宽限期后取消 Worker，由 Worker 向 Runtime 发 SIGTERM 并等待 Harbor 清理。过期 Retry 重新排至 FIFO 队尾。此配置只限制单个进程，并非多进程集群的全局容量；部署时必须按所有进程并发之和做容量预算。

每次仅领取一个可运行 Trial，租约 300 秒、每 30 秒续租。任务包和私密快照落入 Execution 独立目录，然后启动固定 Runtime。Harbor 独占 Sandbox 生命周期，Worker 不创建或销毁 Sandbox。租约丢失或 Worker 被取消时向子进程发送 SIGTERM，并等待 Harbor 的清理过程；不使用 SIGKILL 代替清理。操作系统强制终止或主机掉电仍需平台恢复验收，不能依赖 Python finally 保证处理。

Runtime 只有在 Harbor 原生 result.json 校验及 Task 内容复核通过后写入 native-ready.json。异常且没有该标记时，不伪造评分结果，不重跑同一 Execution，等待 PostgreSQL 的有界过期回收。

上传按独立 Execution 对象路径进行，Manifest 最后发布。上传后原子保存 pending-commit.json，Hub 在租约事务内核对作用域、fencing token、Manifest 和所有文件的摘要/大小，以及 ResultCommit 是否一致；验证失败不写终态。外部读取期间持有 Trial 锁，提交前再次使用数据库时钟检查期限。大对象或存储延迟可能导致超时和重试，当前为受限单 Trial 实现。

恢复已有 native-ready.json 可重试不可变上传；有 pending-commit.json 时可直接重放提交。Hub 已接受但响应丢失时，即使租约已到期，相同结果仍可幂等重放。旧 Execution 不能覆盖新 Retry 的产物或修改新执行状态。

`--check`、离线测试和 Runtime 契约检查不调用模型，也不算真实全链验收。验收状态见 [Worker 验证记录](validation/2026-10-06-s1-worker.md)。
