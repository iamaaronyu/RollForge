# API 说明

| 接口 | 含义 |
|---|---|
| `GET /health/live` | 进程存活，不依赖外部服务 |
| `GET /health/ready` | 检查数据库连通性，不可用时返回 503 |
| `GET /api/v1/platform` | 版本、开发阶段与执行能力状态 |
| `GET /openapi.json` | 自动生成的 API 契约 |
| `GET /docs` | 交互式 API 文档 |
| `POST /api/v1/jobs` | USER 创建单 Trial Job；相同 job_id、相同配置幂等返回 200 |
| `GET /api/v1/jobs/{job_id}` | USER 查询自己的 Job；不存在或其他用户的 Job 返回 404 |
| `POST /api/v1/worker/leases/claim` | WORKER 领取租约；无可领取 Trial 返回 204 |
| `POST /api/v1/worker/leases/renew` | WORKER 续租；校验身份、执行 ID、token 和有效期 |
| `POST /api/v1/worker/leases/finish` | WORKER 提交结果元数据；同一结果可幂等重放 |

业务接口已实现，控制面写接口默认关闭。ROLLFORGE_CONTROL_PLANE_WRITES_ENABLED 默认为 false；关闭时创建、领取、续租、完成返回 503 / WRITES_DISABLED。GET 查询仍需认证。Worker 执行与恢复实现见 [Worker 说明](worker.md)，真实全链验收尚未完成，platform 的 execution_enabled 保持 false，control_plane_writes_enabled 单独报告元数据写开关，不表示完成 S0 或开放真实执行。

## 认证与权限

使用 Authorization: Bearer 认证，ROLLFORGE_API_CREDENTIALS 是由 SecretStr 保存的 JSON 数组。每条配置仅包含 subject_id（UUID）、role（USER 或 WORKER）和 token_sha256（64 位小写十六进制摘要）。原始 token 使用 secrets.token_urlsafe(32) 等安全随机方式生成，仅保存在受限本地文件或凭证系统；不进入配置快照、日志、仓库或前端公开环境变量。

服务对传入 token 计算 SHA256，并用常量时间摘要比较匹配身份。未知、缺失或格式无效的凭证返回 401；角色错误返回 403。请求不能传 owner_id 或 worker_id；服务从认证身份构造这两个字段，额外字段返回 422。Worker 是受信任的平台执行身份，可以领取全局队列中的任务；这里未实现按租户分配 Worker 的策略。

当前认证为启动时加载的静态服务凭证，不是用户登录或 OIDC。配置变更需要重启服务；角色变更与 token 撤销均通过更新配置并重启生效。对外部署前需 HTTPS 和部署权限控制；本阶段验收没有验证 TLS、网关或外部身份提供商。

## 请求、错误与 SDK

请求与响应定义在 packages/schemas。创建只接受 job_id 和 snapshot。续租接受 lease（trial_id、execution_id、fencing_token）及 lease_seconds；完成接受 lease 和 result。租约时间范围为 1–300 秒。每个结果的 manifest_key 必须匹配对应 Execution 的对象路径；可运行快照的提交会验证对象内容、摘要、作用域和 fencing token；元数据快照仍仅校验对象键，不代表真实运行完成。claim 可设置 runnable_only=true，仅领取存在 runtime 绑定的 Trial。

错误体为 ApiError：code 和中文 message。409 区分 CONFLICT（幂等内容冲突）和 LEASE_REJECTED（执行权无效）；422 不回显非法字段或输入值；数据库错误返回 503 / DATABASE_UNAVAILABLE，不输出 SQL 或连接信息。对象存储验证失败返回 503 / STORAGE_UNAVAILABLE，且不提交终态。

Python SDK 的 HubClient 提供 create_job、get_job、claim、renew、finish，以及异步上下文管理。token 使用 SecretStr 传入。空队列转换为 None；HubError 提供 status_code 和经契约校验的 code。SDK 不跟随重定向，不把原始错误响应、认证头或网络异常消息附加到异常。

## OpenAPI 与前端类型

docs/openapi.json 是 API 的离线导出，apps/hub-web/lib/api-types.ts 从该文件生成，不能手改。生成器使用 [openapi-typescript 官方 CLI](https://openapi-ts.dev/cli)。

```sh
make api-types
make check
```

make check 和 CI 检查实际 API 与导出文件一致，前端 typecheck 检查生成类型未过期；修改接口后需同时提交生成文件。实测结果见 [鉴权 API 与 SDK 验收](validation/2026-10-06-s1-api.md)。

健康响应不包含连接字符串或原始基础设施错误。当前就绪检查仍只检查数据库；可运行结果提交依赖对象存储，Redis 尚未接入。

开发 API 绑定本机回环地址；完成鉴权与生产部署加固后再向外提供服务。
