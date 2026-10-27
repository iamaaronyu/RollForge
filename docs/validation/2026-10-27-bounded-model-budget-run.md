# 受限会话预算下的 Registry 双 Trial 验收

日期：2026-10-27。目标节点：`100.106.244.122`。本记录对应本日新发起的一次验收尝试，不替代此前记录的其他成功运行。

## 运行范围

- 从已审核的 Task revision 2、Agent revision 1、Model revision 1 创建一个 Registry 绑定 Job，包含 2 个 Trial，每个最多 1 次 Execution。
- 会话仅在 Mac 可信网关签发，有效期 30 分钟、最多 15 个请求、预算上限 0.05 美元；Token 经 SSH 标准输入写入服务器权限为 `rollforge:rollforge 0600` 的 Runtime 文件，没有进入终端输出或聊天。
- Hub 写入、Worker 和 Scheduler 开关仅为本次尝试临时开启；Web 实验开关保持关闭，Scheduler 并发为 1。

## 结果

Job 与两个 Execution 均创建成功，Registry 选择与快照一致；两个 Trial 最终为 `FAILED`，失败分类均为 `INFRA_ERROR`，Runtime 异常类型均为 `UnknownApiError`。已接受的 Manifest 显示两次执行的输入/输出 Token 用量均为 0，因此未得到模型评分，不能视为真实模型验收通过。

失败定位为会话预算门槛。网关按请求 `max_tokens=8192`，并为缓存计费差异预留 2,000,000 个输入 Token；按代码内费率，单个完整请求的最低预留为：

```text
ceil((2,000,000 × $0.30 + 8,192 × $1.20) / 1,000,000) = $0.609831
```

本次 `$0.05` 上限低于单次请求预留；会话模型与冻结模型名均为 `deepseek-flash`。观测到的失败、零用量和预算规则符合网关在转发前拒绝请求的路径。该会话随后已撤销；`ROLLFORGE_MODEL_SESSION_TOKEN` 已从服务器 Runtime 文件删除，临时 USER 凭证文件和验收脚本已移除。未提高额度或重复调用。

## 收尾状态

- `rollforge-hub.service` 与 `rollforge-web.service` active，Hub readiness 正常；Scheduler inactive。
- Hub 写入、Worker、Scheduler、Web 实验四个开关均为 `false`。
- E2B 活动 Sandbox 数量为 0，Firecracker 进程数量为 0。
- 该尝试不证明双 Trial 真实模型链路通过。后续如继续真实模型验收，须先由用户明确批准高于 `$0.05` 的单次总预算；至少需要能覆盖 `$0.609831` 的单请求预留。预算耗尽时停止，不自动提高额度。
