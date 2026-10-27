# API 说明

| 接口 | 含义 |
|---|---|
| `GET /health/live` | 进程存活，不依赖外部服务 |
| `GET /health/ready` | 检查数据库连通性，不可用时返回 503 |
| `GET /api/v1/platform` | 版本、开发阶段与执行能力状态 |
| `GET /openapi.json` | 自动生成的 API 契约 |
| `GET /docs` | 交互式 API 文档 |
| `POST /api/v1/jobs` | USER 创建 Job 并按 `trial_count` 展开 1–1000 个 Trial；相同输入幂等 |
| `POST /api/v1/jobs/from-revisions` | USER 按 Task/Agent/Model Registry 引用创建 Job；必须匹配部署审核目录 |
| `POST /api/v1/jobs/{job_id}/cancel` | USER 取消 Job 下所有未结束 Trial |
| `POST /api/v1/jobs/{job_id}/trials/{trial_id}/cancel` | USER 取消指定 Trial |
| `GET /api/v1/registry/assets` | USER 查询自己的资产摘要，按 UUID 游标分页 |
| `GET /api/v1/registry/assets/{asset_id}/revisions` | USER 查询资产的不可变版本 |
| `POST /api/v1/registry/revisions` | USER 登记新资产版本；由控制面写开关限制 |
| `GET /api/v1/jobs/{job_id}` | USER 查询自己的 Job；不存在或其他用户的 Job 返回 404 |
| `POST /api/v1/worker/leases/claim` | WORKER 领取租约；无可领取 Trial 返回 204 |
| `POST /api/v1/worker/leases/renew` | WORKER 续租；校验身份、执行 ID、token 和有效期 |
| `POST /api/v1/worker/leases/finish` | WORKER 提交结果元数据；同一结果可幂等重放 |
| `POST /api/v1/worker/leases/recover` | WORKER 有界回收已过期租约；预算耗尽的 Trial 失败，否则重新排队 |

业务接口已实现，控制面写接口默认关闭。ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED 默认为 false；关闭时创建、取消、领取、续租、完成返回 503 / WRITES_DISABLED。GET 查询仍需认证。用户取消会在 PostgreSQL 事务中将未开始 Trial 置为 CANCELLED；运行中的 Trial 同事务撤销 Execution 租约并标记 CANCELLED，Worker 在下次续租（最多约 30 秒）发现租约撤销后通过现有 SIGTERM 路径清理 Runtime。取消与完成由同一 Trial 锁串行化。对已取消 Job/Trial 的重复请求幂等；全部 Trial 已成功/失败的 Job 或已成功/失败的 Trial 不能再取消，返回 409。Job 有部分已结束样本时，仍会取消其余未结束样本。该 API 不等同于即时强制杀进程，真实 E2B 取消时延与清理仍需运行验收。Worker 执行与恢复实现见 [Worker 说明](worker.md)，受限 LAN、并发 1 的合成样例真实全链已通过。网关请求次数已持久化，但 TLS、部署身份认证、用户登录与 token 级费用预算仍未完成；platform 的 execution_enabled 保持 false，control_plane_writes_enabled 单独报告元数据写开关，不表示完成 S0 或开放真实执行。

## 认证与权限

使用 Authorization: Bearer 认证，ROLLFORGE_API_CREDENTIALS 是由 SecretStr 保存的 JSON 数组。每条配置仅包含 subject_id（UUID）、role（USER 或 WORKER）和 token_sha256（64 位小写十六进制摘要）。原始 token 使用 secrets.token_urlsafe(32) 等安全随机方式生成，仅保存在受限本地文件或凭证系统；不进入配置快照、日志、仓库或前端公开环境变量。

服务对传入 token 计算 SHA256，并用常量时间摘要比较匹配身份。未知、缺失或格式无效的凭证返回 401；角色错误返回 403。请求不能传 owner_id 或 worker_id；服务从认证身份构造这两个字段，额外字段返回 422。Worker 是受信任的平台执行身份，可以领取全局队列中的任务；这里未实现按租户分配 Worker 的策略。

当前认证为启动时加载的静态服务凭证，不是用户登录或 OIDC。配置变更需要重启服务；角色变更与 token 撤销均通过更新配置并重启生效。对外部署前需 HTTPS 和部署权限控制；本阶段验收没有验证 TLS、网关或外部身份提供商。

## 请求、错误与 SDK

请求与响应定义在 packages/schemas。创建只接受 job_id 和 snapshot。续租接受 lease（trial_id、execution_id、fencing_token）及 lease_seconds；完成接受 lease 和 result。租约时间范围为 1–300 秒。每个结果的 manifest_key 必须匹配对应 Execution 的对象路径；可运行快照的提交会验证对象内容、摘要、作用域和 fencing token；元数据快照仍仅校验对象键，不代表真实运行完成。claim 可设置 runnable_only=true，仅领取存在 runtime 绑定的 Trial。

错误体为 ApiError：code 和中文 message。409 区分 CONFLICT（幂等内容冲突）和 LEASE_REJECTED（执行权无效）；422 不回显非法字段或输入值；数据库错误返回 503 / DATABASE_UNAVAILABLE，不输出 SQL 或连接信息。对象存储验证失败返回 503 / STORAGE_UNAVAILABLE，且不提交终态。

Python SDK 的 HubClient 提供 create_job、cancel_job、cancel_trial、get_job、claim、renew、finish，以及异步上下文管理。token 使用 SecretStr 传入。空队列转换为 None；HubError 提供 status_code 和经契约校验的 code。SDK 不跟随重定向，不把原始错误响应、认证头或网络异常消息附加到异常。

## OpenAPI 与前端类型

docs/openapi.json 是 API 的离线导出，apps/hub-web/lib/api-types.ts 从该文件生成，不能手改。生成器使用 [openapi-typescript 官方 CLI](https://openapi-ts.dev/cli)。

```sh
make api-types
make check
```

make check 和 CI 检查实际 API 与导出文件一致，前端 typecheck 检查生成类型未过期；修改接口后需同时提交生成文件。实测结果见 [鉴权 API 与 SDK 验收](validation/2026-10-06-s1-api.md)。

健康响应不包含连接字符串或原始基础设施错误。当前就绪检查仍只检查数据库；可运行结果提交依赖对象存储，Redis 尚未接入。

开发 API 绑定本机回环地址；完成鉴权与生产部署加固后再向外提供服务。

## 页面查询与审核任务

新增 USER 接口：`GET /api/v1/jobs`（UUID 游标分页）、`GET /api/v1/jobs/{job_id}/executions`（执行 token 游标分页）、`GET /api/v1/tasks/approved`（仅 id/label）、`POST /api/v1/jobs/from-approved-task`（job_id/task_id，由服务端选冻结快照）。列表不返回快照，执行历史不返回 Worker 身份；跨用户历史为 404。分页 limit 为 1–100，next_cursor 为 null 表示最后一页。Job ID 排序不代表时间排序。

审核目录是部署者提供的私密启动配置，不是持久化 Registry。界面和边界见 [本机任务界面](web-console.md)，后续实施顺序见 [实施进度](next-implementation.md)。正式入口与写开关仍分别控制，浏览器不能自由指定 runtime 配置。

产物 USER 接口：`GET /api/v1/jobs/{job_id}/executions/{execution_id}/artifacts` 返回已接受结果的 ArtifactIndex；`GET .../artifact-text?path=<相对文件路径>` 返回经摘要核验的纯文本。未接受/跨用户为 404，大小超过 1 MiB 为 413，二进制/非 UTF-8 为 422，篡改或存储故障为安全 503。对象键由数据库执行身份生成，不接受客户端对象键。

`GET .../artifact-media?path=<Manifest 中相对文件路径>` 仅为图片和音频提供最多 8 MiB 的已验摘要内容，执行所有权、文件清单和摘要均在 Hub 复核；不支持的扩展名为 415，超限为 413。服务不接受 URL/对象键，也不签发对象存储访问地址。

`GET .../artifact-download?path=<Manifest 中相对文件路径>` 下载一个已接受执行的产物，最大 64 MiB。服务先核对 Manifest 精确路径，再完整读取并校验大小与 SHA256，以 `application/octet-stream` 和安全附件文件名返回；缺失文件为 404，超限为 413。Web 控制台通过同源代理转发，Hub token 和对象存储地址不会进入浏览器。

`GET .../trajectory` 返回共享 TrajectoryView（projection_version=2），投影已接受执行的固定主轨迹与有界内嵌子轨迹。权限及摘要核验与文本预览一致；媒体引用只返回与接受 Manifest 匹配的文件路径，外部 URL/主机路径和外部子轨迹位置不返回。未知 ATIF 版本、非连续步骤、无效调用引用、重复子轨迹 ID、超过 1000 个单轨迹步骤或总投影预算返回 422。不会将解析错误中的原始内容返回客户端。

注册表提供 USER 的资产列表、版本创建/读取/分页接口，写入受现有控制面开关约束；版本不可修改，注册不授予执行权限。网页入口见 `/registry`，详细契约和摘要语义见 [Registry](registry.md)。

JobCreateRequest 与 ApprovedJobCreateRequest 支持 `trial_count`（1–1000，省略默认为 1）。创建事务确定性生成 attempt_index 为 0..N-1 的 Trial；相同 job_id、快照和样本数幂等返回，输入不一致冲突。`GET /api/v1/jobs/{job_id}/trials` 按样本序号分页。Job 级 Execution 游标使用跨样本单调递增的 execution_sequence，响应同时提供 attempt_index 与样本内 fencing_token；因此旧单 Trial 客户端中，历史记录的游标值与 fencing token 相同。

Worker 可用租约回收接口请求处理 1–1000 条已过期记录（默认 100）。Hub 在 PostgreSQL 中重新校验期限、状态并锁定 Trial/Execution；未耗尽预算的 Trial 重新入队，耗尽时置为 FAILED。`queued_at` 记录入队和重试时间，队列按 queued_at、Job ID、样本序号稳定 FIFO 排序。用户角色无权执行恢复。
