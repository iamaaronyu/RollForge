# Harbor 到 Tailscale 模型网关的受限恢复验收

日期：2026-10-26。目标为 tyu-pc-linux 的固定 Harbor 0.24.0、E2B SDK 2.46.0、Claude Code 2.1.81。仅执行公开 hello-task，独立 Verifier；没有开启 Hub 写入、Worker/Scheduler 或 Web 实验执行开关。本轮是直接 Harbor 验收，不是已部署 Hub→Worker→对象存储全链验收。

## 问题与证据

最初真实执行在 Agent 阶段发生 AgentTimeoutError，未生成 answer.txt，Verifier 为 0。网关账本用量未增加。进一步检查说明不能仅靠增加超时解决，也不能仅添加 CIDR 例外：

1. 固定 E2B 默认拒绝 `100.64.0.0/10`，网关 `100.102.201.53` 位于其中；orchestrator 原配置没有额外允许 CIDR。
2. 节点 nat PREROUTING 将 Sandbox 的 TCP 重定向到 E2B egress proxy，包括非 80/443 的连接；仅添加私网允许集合并不能使请求走 Tailscale 直连。
3. 现有 E2B MASQUERADE 仅覆盖出口 enp5s0，到网关的路由实际使用 tailscale0；直连需要对应的受限源地址转换。
4. 最终包头诊断显示，源 `10.11.0.1` 的 SYN 已转换成 Linux Tailscale 地址并从 tailscale0 发出，但 Mac 返回 RST。随后确认 Mac TCP 11441 没有监听进程。重新以指定 Tailscale 地址启动受限网关后，匿名请求与真实模型调用才通过。

期间的网络探针在 Agent 执行前主动中止，没有盲目重复消耗模型额度。包头诊断未使用载荷输出选项，原始日志只保留在受保护或 Git 忽略目录。

## 本轮限定修复

在无活动 Firecracker 的情况下，核对真实 Compose 目录和原始文件摘要，使用独立临时 override 设置 `ALLOW_SANDBOX_INTERNAL_CIDRS=100.102.201.53/32`，不覆盖既有 override。该设置影响节点所有 Sandbox，因此另加端口限制：

- raw PREROUTING：仅允许 `veth+` 到该地址 TCP 11441，丢弃同地址其他端口。
- nat PREROUTING：仅对上述目的地址/端口和 ingress 添加直连 ACCEPT，避开内置 TCP REDIRECT；其他目的流量保持原代理路径。
- nat POSTROUTING：仅源 `10.11.0.1–10.11.0.33`、出口 tailscale0、同一目的地址 TCP 11441 做 MASQUERADE。
- 网关只绑定 Mac 的 `100.102.201.53:11441`；负向探针仅绑定同地址 11442，返回公开常量，不提供文件。
- 原会话已经过期，临时刷新 30 分钟；保持同一 token 身份、SQLite 已用计数和总上限 20，不清空或重建账本，结束恢复原过期时间。

主机可访问负向探针并返回 200，随后 Sandbox 内同端口不可达，从而避免把未启动监听器误判为隔离成功。

## 真实结果

最终运行输出根目录为 `/var/lib/rollforge/harbor-fixed-a514a57cc8c34385b41fb19203836e58`，权限受限，不提交原始输出。

| 项目 | 结果 |
|---|---|
| Sandbox 匿名 POST 到模型网关 | HTTP 403，表明连接到达且无认证被拒 |
| Sandbox 到网关其他端口 11442 | 不可达；主机同端口 200 |
| 真实上游请求 | 2 次 HTTP 200，经固定 DeepSeek 官方 Anthropic 接口 |
| Harbor 原生结果 | reward 1.0，exception_info 为空 |
| 产物 | answer.txt 字节严格等于 `RollForge ready` 加换行 |
| Verifier | 独立环境运行 |
| 输出凭证扫描 | 13 个文件，已检查的 Hub/Worker/Runtime 实际凭证值 0 命中 |
| 资源 | Firecracker 0 |
| 请求账本 | 原已用 1，本轮新增 2，累计 3/20；未增加总预算 |

成功依据是原生结果、评分、答案和清理证据，不以子进程退出码或存在 result.json 作为成功标准。

## 恢复与后续边界

本轮结束撤销专属 raw/NAT 规则及临时 Compose override，恢复原 orchestrator/API 配置；停止新建网关和负向探针，恢复会话原过期时间，保留 SQLite 消费计数。恢复后独立 SDK 列出 Sandbox 为 0，Firecracker 为 0；专属 raw/NAT 规则均为 0，CIDR/临时 override 均不存在，Hub/Web 运行、Scheduler inactive，E2B API/orchestrator healthy。原 override SHA256 复核为 `b723ef742e8860004cdf79b593f57c3dabdaea089d2e737f6f270019aec01aca`。

这是临时受控方案验证，结束后不保留 Sandbox 到网关的放行。持续执行前需要把网关进程托管、健康检查、严格网络例外及恢复顺序纳入明确的部署方案，并重新确认短期会话预算；不能只添加 CIDR 或永久放行整个 Tailscale 网段。未知任务、多租户、TLS、线上预算灾备和当前部署的完整 Worker 链仍未因此通过验收。

本轮固定 Runtime 契约检查为 9 passed。共享工作区 make check 曾通过，但同时有另一聊天继续编辑 Viewer/API；此处不将该次检查作为其未完成改动的验收或提交依据。本提交仅记录上述真实运行和环境恢复事实。
