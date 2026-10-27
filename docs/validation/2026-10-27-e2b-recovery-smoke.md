# E2B 主机恢复与 SDK smoke 验收

日期：2026-10-27。通过项目恢复脚本对真实 Linux E2B 主机执行 `--apply`；仅做服务恢复和无模型 SDK Sandbox 创建/销毁，不运行 RollForge Worker 或模型。

## 执行前检查

- 恢复脚本只读预检通过：Linux x86_64、内核 6.8.0-138、12 CPU、7.57 GiB RAM、209–210 GiB 空闲空间，固定 Compose 摘要匹配且 `errors=[]`。
- 主机内存低于建议值，因此仅限并发 1 的受限实验。执行前无 Firecracker 进程；Hub 写入和执行开关关闭。

## 执行与结果

- `scripts/recover_e2b_host.py --allow-low-memory --apply` 完成固定 Compose `up -d --wait` 和官方 SDK smoke，输出 `services_and_sdk_smoke_passed`。
- smoke 后 E2B API 与 Orchestrator 均 healthy；Firecracker 数量为 0；Sandbox 已由 SDK smoke 清理。
- Loopback 控制端口防护存在；`ALLOW_SANDBOX_INTERNAL_CIDRS` 未配置；Hub 写入与执行开关仍为 false。
- 没有启动 RollForge Worker/Scheduler，没有签发模型会话或调用模型；未验证宿主重启后的自动恢复。

## 未完成边界

该结果只证明本次手动恢复流程与无模型 SDK smoke 可用。真实双 Worker/多 Trial 故障恢复、主机重启、持久网络规则、加密备份及隔离恢复仍待验收。持久 `/32` allowlist 会影响节点上的所有 Sandbox；在确认节点独占 RollForge 前保持关闭。

## 2026-10-27 再次验证

- 通过 `scripts/recover_e2b_host.py --allow-low-memory` 只读检查：Compose 摘要匹配、`errors=[]`，内核 6.8.0-138、12 CPU、7.57 GiB 内存、207.12 GiB 空闲空间。当前 boot ID 为 `87fa6de1-53fe-4016-9e88-d33ab75d7546`；本次未重启主机，不将当前 boot ID 当成重启恢复证据。
- `scripts/recover_e2b_host.py --allow-low-memory --apply` 返回 `services_and_sdk_smoke_passed`。E2B Compose 服务随后均为运行态；复核 Firecracker 数量为 0。
- `rollforge-e2b-gateway-firewall.service` 为 inactive，未发现其专属持久 iptables 规则，Compose override 中没有 `ALLOW_SANDBOX_INTERNAL_CIDRS`。此次 smoke 未更改模型网关 allowlist，没有启动 RollForge Worker、调用模型或打开 Hub/Web 写入门。
- Linux 到 Mac 固定网关 `100.102.201.53:11441` 的无会话、有效格式请求返回 HTTP 403。此探测在鉴权阶段被拒绝，不代表 Sandbox 网络连通性或真实模型调用验收。受保护配置校验报告 Runtime 尚缺 `ROLLFORGE_MODEL_SESSION_TOKEN`；尚未签发会话。
