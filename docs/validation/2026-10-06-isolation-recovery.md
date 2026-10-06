# 2026-10-06 凭证隔离与部署恢复验收

## 当前结论

S0 仍未完成，Hub 执行入口保持关闭，暂不进入 S1。以下区分已获得的证据与尚未通过的关卡。

## 隔离探测

`examples/run_isolation_acceptance.py --run` 使用真实 Harbor 0.24.0、E2B 2.46.0、Claude Code 2.1.81 与 DeepSeek。测试仅记录布尔值，禁止将凭证值输出；输入、原始结果和证据仅留在忽略目录。

直接连接官方模型 API 的基线运行得到 reward=1.0：

- Agent 的工具命令可读取 ANTHROPIC_API_KEY 环境变量。这是固定版本 Harness 的凭证传递方式，属于待解决的隔离缺口。
- Agent 中没有 E2B_API_KEY；Verifier 进程环境中没有模型或 E2B 凭证。
- Agent 开始时及探测时均看不到私密测试 canary；Verifier 执行时可以看到。
- 对本次 16 个原始输出文件逐字节检查，未匹配到 .env.spike 中的真实凭证值。结束后 Sandbox 为 0。

这些结果只证明已测试时点的文件可见性和本次输出无密钥命中；同一 Sandbox 中运行 Verifier 不能提供对 Agent 遗留后台进程的强隔离。

进一步运行 `--separate-verifier`，使用 Harbor 原生 separate 模式实测通过：Verifier 开始事件中 SDK 查询 Sandbox 为 0，确认 Agent Sandbox 已删除；新 Verifier 环境得到 reward=1.0，测试可见、凭证环境为空，结束后 Sandbox 为 0。本次总耗时 11.21 秒，16 个输出文件无真实凭证命中。Task digest 为 sha256:09ba54eb448de3eb7ef4b3b878c20b3c054a419ecc1c59e503fb4673ee3785b2。总体隔离验收仍返回失败，因为直接运行的 Agent 能读到真实模型 Key。

## 测试模型网关

`scripts/model_gateway.py` 在可信 Mac 宿主持有真实上游密钥，只转发到固定 DeepSeek Anthropic Messages 接口。Sandbox 只使用短期会话凭证。按会话限制模型、有效期、请求次数和单次输出 token；支持读取配置后立即撤销，拒绝共享可读的配置文件，不记录请求或认证头。

上游 Key 配置必须存放在忽略目录，文件权限 0600，父目录 0700。会话 token 可以被 Agent 使用，因此并非无凭证 Agent；隔离目标是保护真实上游 Key，并限制会话的滥用范围。

五项网关单元检查通过：未知会话/其他模型拒绝、过期拒绝、并发限次原子性、运行中撤销、配置权限拒绝。这些离线检查不替代真实主链验收。

本机网关实际调用 DeepSeek 返回 HTTP 200 和 message；局域网宿主无认证请求返回 403。实际撤销会话后，本机请求返回 403。Sandbox 到网关的实际试验仍发生连接超时，未取得模型响应，记录为 RUNTIME_FAILED / AgentTimeoutError，不能当作安全验收通过。诊断覆盖默认网络、网关 IP 放行、临时路由例外与标准 HTTP 端口中继，仍未解决；不将这些临时诊断配置作为部署方案。

当前网关为实验实现：进程重启会重置内存中的计数；局域网入口为 HTTP；尚无部署服务、持久化预算或生产认证。这些能力必须在安全执行入口开放前补齐。

自动审批拒绝了复制含真实上游 Key 的配置到 Linux 主机，未执行该复制。后续方案将真实 Key 留在 Mac，仅通过网关提供受限会话。临时会话已撤销，测试网关、中继、模型反向隧道和路由例外已清理，生成的私密配置已删除。原有 E2B 隧道和服务保留。

## 恢复流程与验收范围

恢复前确认没有活动 Sandbox，按固定 Compose 提交执行 `docker compose down`，再 `docker compose up -d`。不使用 -v，不删除模板、数据库卷或镜像。原始部署日志可能包含 E2B Key，必须以 umask 077 保存到忽略的本地日志，不直接打印。

服务恢复后所有十个常驻服务恢复运行，其中九个具有健康检查的服务均 healthy，ready 没有健康检查。官方 smoke exit 0；随后缓存模板上的真实 Claude Code + DeepSeek + 独立 Verifier 运行评分为 1.0，无 Sandbox 遗留。

主机重启仍待执行，需要复检内核、KVM、大页池、磁盘、控制端口防火墙及 SSH 隧道；服务停止/启动成功不能代替主机重启验收。SSH 账户无法免密 sudo，完整重启需要 owner 在 Linux 端执行并告知。

## 进入 S1 的条件

网关真实 Sandbox 链路通过，真实上游密钥不进入 Sandbox/快照/输出；私密 Verifier 在独立环境验收；部署恢复和主机重启复检完成。随后实现 PostgreSQL Job/Trial/Execution、不可变快照、鉴权、事务租约、fencing 和恢复调度，以真实双 Worker 竞争验证。

## 代码验证

make check 通过：50 项测试、Python lint/格式、前端类型与构建。make runtime-check 通过：8 项真实依赖契约检查。七个待公开代码/文档文件未匹配到本地真实凭证值。运行输出、原始规划和私密配置不提交。
