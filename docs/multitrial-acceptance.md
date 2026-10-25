# 真实多 Trial 验收

`examples/run_multitrial_acceptance.py` 提供受控的真实 Hub/Worker/Harbor/E2B 多样本验收入口。默认只显示离线说明；必须显式 `--run` 才会提交 Job。首轮固定 2 个 Trial、每个最多 1 次 Execution，建议 Scheduler 并发设为 1，以适配当前 8 GiB Linux 主机。

## 前置条件

- Hub API、PostgreSQL、对象存储已启动，USER/WORKER 凭证相互独立；Hub 写入开关开启，Scheduler 和 Worker 的执行开关仅在受控实验期间开启。
- Scheduler 使用单进程并发 1，并配置固定 Runtime、受限模型会话、E2B 和对象存储。Sandbox 只收到受限会话和 E2B Key；Hub/对象存储凭证留在 Worker。
- Task、Agent、Model revision 已登记，且组合精确匹配部署者审核的运行目录。任务归档已上传并核验。
- 主机已验证无其他活动 Sandbox。真实模型会按每个 Trial 实际调用，注意费用及 Sandbox 清理状态。
- `.env.acceptance` 使用 [示例模板](../.env.acceptance.example) 创建，填入 USER token 和三组 revision 引用，权限设为 0600。API 地址必须为 HTTPS，只有 loopback 可使用 HTTP。文件不提交仓库。

## 执行与判定

```sh
cp .env.acceptance.example .env.acceptance
chmod 600 .env.acceptance
# 本机填入已审核任务的私有 Revision 引用和 USER token 后：
uv run python examples/run_multitrial_acceptance.py --run --env-file .env.acceptance
```

入口会通过 `POST /api/v1/jobs/from-revisions` 创建 Job，轮询 Job/Trial/Execution，直到终态或超时。成功要求：Registry 引用原样固定到快照、两个逻辑 Trial 都 COMPLETED 且 reward=1.0、Attempt 为 0/1、各有且只有一个 COMPLETED Execution、Execution ID 与 Manifest key 各自唯一。Job ID 可通过 `--job-id` 重放，避免创建响应丢失时重复开 Job。

脱敏证据写入忽略目录 `outputs/acceptance/`，仅含状态、Attempt、评分和唯一性计数；不收集日志、模型输入或原始产物。该入口不验证 Sandbox/Firecracker 清零，也不验证双 Worker 并发；作业结束后需按主机运行手册独立检查两者为 0。8 GiB 主机不得为了并发验收直接将 Scheduler 调至 2。

没有可用的 USER/WORKER/S3 配置或受限模型会话时，离线检查和 PostgreSQL 测试不能替代真实执行；应保留执行开关关闭并记录缺少的配置名。
