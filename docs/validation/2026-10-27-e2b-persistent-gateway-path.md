# E2B 持久模型网关网络路径验收

日期：2026-10-27。目标节点：`100.106.244.122`。E2B Compose 固定版本：`08436994a1c0bd48a7f03a08c020bf7a4a673dfa`。

用户确认该节点仅供 RollForge Sandbox 使用后，启用了 Orchestrator 到可信 Mac 网关 `100.102.201.53/32` 的持久 allowlist，并启用 `rollforge-e2b-gateway-firewall.service`。原有 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 环境配置已保留。systemd 环境文件仅含网关 IP，权限为 `root:rollforge 0640`；服务已 enabled/active，标记防火墙规则共 4 条：Sandbox veth 仅可到网关 TCP 11441，其余到该 IP 的流量被拒绝，连接通过 `tailscale0` 做目标端口 SNAT。

E2B Compose 重建后，所有常驻服务均为运行态，API 与 Orchestrator healthy。官方主机只读预检仍通过：固定 Compose 摘要匹配、`errors=[]`，没有活动 Firecracker。

## Sandbox 实测

通过服务器 Runtime venv 使用 E2B SDK 创建一个临时 Sandbox，Sandbox 未接收模型 Token、E2B Key 或外网权限。Sandbox 对网关 `/v1/messages` 发送合法格式但不带 Authorization 的请求，收到 **HTTP 403**。随后 `kill()` 成功；E2B API 列表为 0，主机 Firecracker 进程为 0。

这证明持久网络路径可达且网关拒绝无凭证请求；没有签发有效会话、调用 DeepSeek 上游或启动 RollForge Worker/Scheduler。Hub 控制面写入、Worker 执行、Scheduler 和 Web 实验开关仍全部为 `false`。下一关需用短时限额会话验收真实模型与 Registry 绑定的双 Trial Job，并在结束时撤销会话及关闭所有临时执行开关。
