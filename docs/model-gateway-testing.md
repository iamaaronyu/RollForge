# S0 模型网关受限测试

本流程只适用于可信局域网中的独占 E2B 测试环境，并发 1。上游模型 Key 留在可信网关宿主，不复制到 E2B 主机或 Sandbox。网关仍是实验程序，HTTP 会话传输及进程内计数不满足生产部署要求。

## 网络配置依据

固定 Compose 提交为 `08436994a1c0bd48a7f03a08c020bf7a4a673dfa`，运行二进制对应源码提交为 `7278c2a380767c9989da73cf4c04b1af1b32da18`。

该版本先执行预定义私网拒绝规则，再执行用户出站规则；普通 `allow_out` 不能覆盖私网拒绝。官方网络 Config 暴露 `ALLOW_SANDBOX_INTERNAL_CIDRS`，加入的地址进入更早生效的预定义允许集合。依据为 [pool.go](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/orchestrator/pkg/sandbox/network/pool.go)、[firewall.go](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/orchestrator/pkg/sandbox/network/firewall.go) 和 [私网范围定义](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/shared/pkg/sandbox-network/firewall.go)。

这是 orchestrator 范围的配置，影响该节点的所有 Sandbox，不是每 Trial 的允许规则。不能仅配置 /32 就宣称端口隔离。

## 测试流程

1. SDK 确认没有活动 Sandbox，停止其他真实执行测试。
2. 在 Linux 先设置受限防火墙：针对 Sandbox 的 `veth+` 流量、网关单个地址，raw PREROUTING 先允许 TCP 11441，再丢弃到该地址的其他流量。保留现有规则，不清空链；使用本次测试专属 comment，结束后按完整规则删除。
3. 新建临时 Compose override，仅将 orchestrator 的 `ALLOW_SANDBOX_INTERNAL_CIDRS` 设为网关 IPv4 `/32`。禁止填写整个局域网网段。合并已有 override，重新创建 orchestrator；等待 healthy 和节点注册完成。
4. 可信网关宿主创建 0700 忽略目录及 0600 配置。`upstream_key` 是真实 Key；`sessions` 以随机 token 为键，值包括 `model`、`expires_at` 和 `max_requests`。不要把配置内容写到终端或提交。
5. 运行 `scripts/model_gateway.py --config <私密配置路径> --host <网关地址> --port 11441`，日志仅记录状态码。Sandbox 环境的 `ANTHROPIC_API_KEY` 设置为会话 token，模型 base URL 指向网关。设置 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`；固定 Harbor 已支持传入该变量，超过网关输出预算的请求会被拒绝。
6. 执行 `examples/run_isolation_acceptance.py --run --separate-verifier --env-file <私密会话环境文件>`。可重复添加 `--blocked-url`，验证网关其他端口及控制面地址不可达。负向端口必须先从 Linux 宿主验证可访问，防止把服务未启动误判为网络隔离。
7. 验收要求：无认证网关请求 403；真实模型调用评分 1.0；Agent 无真实上游 Key、E2B Key 和私密测试；独立 Verifier 无凭证；原始输出无凭证命中；结束后 Sandbox 和 Firecracker 为 0。
8. 撤销会话并验证 403。恢复不含网关 override 的 orchestrator，删除本次专属防火墙规则与临时 override；停止测试监听器，删除私密网关配置和会话环境文件。保留受保护的本地证据，不提交原始输出。

主机重启还需要单独验收。以上流程不会使临时防火墙或测试网关自动持久化，也不会开启 Hub 执行入口。
