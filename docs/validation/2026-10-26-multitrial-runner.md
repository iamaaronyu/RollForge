# 真实多 Trial 验收入口记录

日期：2026-10-26。实现通过受控 Registry revision 创建 2–100 个 Trial Job、轮询状态并核验独立执行/产物的显式验收脚本。该记录验收的是工具与检查逻辑，不表示已完成真实 Harbor/E2B 多 Trial 执行。

## 实现与保护

- 默认命令只显示离线模式说明；需显式 `--run` 才会访问 Hub 并创建 Job。
- 从权限为 0600 的 `.env.acceptance` 读取 USER token 和三类 Registry 引用。HTTP API 仅允许 loopback；其他地址必须 HTTPS。示例文件只含空值并公开提交；私有副本已被 `.env.*` 忽略规则覆盖。
- 使用 `POST /api/v1/jobs/from-revisions`，并核对返回快照的 Registry 引用与请求一致。网络响应丢失时可用同一 `--job-id` 幂等重试。
- 成功必须满足每个 Trial 完成且 reward=1.0、Attempt 索引连续、每个 Attempt 正好一个 Completed Execution，Execution ID 与 Manifest key 唯一。脱敏 JSON 证据仅写入忽略的 `outputs/acceptance/`，权限限制为 owner。
- 建议当前 8 GiB Linux Sandbox 主机使用 Scheduler 并发 1 做两个真实 Trial 的顺序验收；脚本不声称覆盖双 Worker、Sandbox 清零或 Firecracker 清零，结束后需单独核对。

## 验证

- `tests/unit/test_multitrial_acceptance.py`：2 passed，覆盖独立评分/产物判定和 Hub token URL 传输策略。
- `examples/run_multitrial_acceptance.py` 无 `--run` 离线执行通过，确认不访问 Hub、Worker、Harbor 或模型。
- 最终 `make check`：173 passed、16 skipped、1 项既有 Starlette/httpx 弃用警告；真实 PostgreSQL 测试通过，OpenAPI、前端类型和 Next.js 生产构建通过。16 项跳过边界见 [Registry 页面验收](2026-10-26-registry-ui.md)。
- 本轮没有 Hub/Worker/S3 凭证及受限模型会话配置，因此未运行真实多 Trial、Harbor、E2B 或模型。

## 后续要求

准备独立 USER/WORKER token、对象存储最小权限、固定运行快照及短期受限 DeepSeek 会话后，按 [多 Trial 验收流程](../multitrial-acceptance.md) 执行；之后再安排资源充足主机上的双 Worker/并发 2 与中断恢复验收。
