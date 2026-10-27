# S0 模型网关受限测试

本流程只适用于可信局域网中的独占 E2B 测试环境，并发 1。上游模型 Key 留在可信网关宿主，不复制到 E2B 主机或 Sandbox。请求预算已使用 SQLite 跨进程、重启原子持久化；网关仍未提供 TLS、部署身份认证、token 计费或生产级密钥管理。

## 网络配置依据

固定 Compose 提交为 `08436994a1c0bd48a7f03a08c020bf7a4a673dfa`，运行二进制对应源码提交为 `7278c2a380767c9989da73cf4c04b1af1b32da18`。

该版本先执行预定义私网拒绝规则，再执行用户出站规则；普通 `allow_out` 不能覆盖私网拒绝。官方网络 Config 暴露 `ALLOW_SANDBOX_INTERNAL_CIDRS`，加入的地址进入更早生效的预定义允许集合。依据为 [pool.go](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/orchestrator/pkg/sandbox/network/pool.go)、[firewall.go](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/orchestrator/pkg/sandbox/network/firewall.go) 和 [私网范围定义](https://github.com/e2b-dev/infra/blob/7278c2a380767c9989da73cf4c04b1af1b32da18/packages/shared/pkg/sandbox-network/firewall.go)。

这是 orchestrator 范围的配置，影响该节点的所有 Sandbox，不是每 Trial 的允许规则。不能仅配置 /32 就宣称端口隔离。

## 测试流程

1. SDK 确认没有活动 Sandbox，停止其他真实执行测试。
2. 在 Linux 先设置受限防火墙：针对 Sandbox 的 `veth+` 流量、网关单个地址，raw PREROUTING 先允许 TCP 11441，再丢弃到该地址的其他流量。保留现有规则，不清空链；使用本次测试专属 comment，结束后按完整规则删除。
3. 新建临时 Compose override，仅将 orchestrator 的 `ALLOW_SANDBOX_INTERNAL_CIDRS` 设为网关 IPv4 `/32`。禁止填写整个局域网网段。合并已有 override，重新创建 orchestrator；等待 healthy 和节点注册完成。若随后 Sandbox 创建返回 placement 503，先确认没有活动 Sandbox/Firecracker，再从 Compose 目录执行 `docker compose restart api`，等待 API healthy 后重试。该恢复顺序已在 2026-10-27 的远端无模型网络验收中复核；不要在有活动 Trial 时重启 API。
4. 可信网关宿主创建 0700 忽略目录及 0600 配置。`upstream_key` 是真实 Key；`sessions` 以随机 token 的 SHA-256 为键，值包括 `model`、`expires_at` 和 `max_requests`。不要把原始会话 token 写入配置，也不要把配置内容写到终端或提交。网关启动时就在同目录创建权限 0600 的 `<配置名>.sqlite3` 预算账本；即使尚未发生模型请求，也能形成明确的空预算状态。备份/恢复时需将配置和账本作为一组处理。重启不能重置已用次数。
5. 运行 `scripts/model_gateway.py --config <私密配置路径> --host <网关地址> --port 11441`，日志仅记录状态码。Sandbox 环境的 `ANTHROPIC_API_KEY` 设置为会话 token，模型 base URL 指向网关。设置 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`；固定 Harbor 已支持传入该变量，超过网关输出预算的请求会被拒绝。
6. 执行 `examples/run_isolation_acceptance.py --run --separate-verifier --env-file <私密会话环境文件>`。可重复添加 `--blocked-url`，验证网关其他端口及控制面地址不可达。负向端口必须先从 Linux 宿主验证可访问，防止把服务未启动误判为网络隔离。
7. 验收要求：无认证网关请求 403；真实模型调用评分 1.0；Agent 无真实上游 Key、E2B Key 和私密测试；独立 Verifier 无凭证；原始输出无凭证命中；结束后 Sandbox 和 Firecracker 为 0。
8. 撤销会话并验证 403。恢复不含网关 override 的 orchestrator，删除本次专属防火墙规则与临时 override；停止测试监听器，删除私密网关配置和会话环境文件。保留受保护的本地证据，不提交原始输出。

主机重启还需要单独验收。以上流程不会使临时防火墙或测试网关自动持久化，也不会开启 Hub 执行入口。

## 独占测试节点的持久网络服务

生产/常驻 Worker 只能在专属 RollForge E2B 节点使用此配置；E2B 的 `/32` allowlist 对节点上所有 Sandbox 生效，不能用于共享或多租户节点。真实上游 Key 留在可信 Mac 的 `~/.rollforge/private/gateway.json`（目录 0700、文件 0600），不要复制到 Linux/E2B 主机。Mac 的 `com.rollforge.model-gateway` LaunchAgent 将网关绑定到 Mac Tailnet IPv4 `100.102.201.53:11441`；会话应默认为空，只在受控运行前短时签发。

Linux E2B Compose 的 `compose.override.yaml` 仅为 orchestrator 增加 `ALLOW_SANDBOX_INTERNAL_CIDRS: 100.102.201.53/32`。部署 `/etc/rollforge/e2b-gateway.env`（只含非敏感的 `ROLLFORGE_MODEL_GATEWAY_IP=100.102.201.53`）和 `infra/systemd/rollforge-e2b-gateway-firewall.service`。该单元在 Docker、Tailscale 接口及 Orchestrator healthy 后调用 `scripts/e2b_gateway_firewall.py`，只允许 E2B `veth+` 到该网关 TCP 11441，丢弃到同一 IP 的其他端口，并仅为该目标端口做 Tailscale SNAT。规则带固定注释、可幂等启停；停止单元会删除它拥有的规则。

启用前确认 Firecracker 为 0、Mac 网关无有效会话，并验证 E2B API 与 Orchestrator 健康。启用后用无效会话验证 Sandbox 收到 403，再分别验收有效会话、到期/撤销和预算持久性。关停时先停 Worker/Scheduler、撤销会话，再停止防火墙单元并移除 Compose allowlist、重建 Orchestrator 和刷新 API。主机重启后须重新验收；所有 Hub 写入、Worker、Scheduler 与 Web 实验开关继续保持关闭，直到真实端到端及灾备关卡全部通过。
