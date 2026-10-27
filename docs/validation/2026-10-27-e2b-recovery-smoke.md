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
