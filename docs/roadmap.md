# 开发路线图

1. **S0**：Claude Code + DeepSeek V4.1 Flash + 自托管 E2B 真实链路。
2. **S1**：单任务 Job、Trial/Execution 持久化、事务租约、fencing、幂等提交与有界恢复。
3. **S2**：版本化结果 Manifest、原始输出、上传恢复与 Trial 页面。
4. **S3**：不可变 Task/Dataset Revision、Registry、FIFO/容量调度与 Job 页面；从 1/2 并发逐步验证到 100 个逻辑 Trial。
5. **S4**：取消与清理、故障测试、监控、部署与运行手册，完成 MVP 验收。
6. 后续：企业治理、实验比较、数据挖掘和训练导出。

2026-10-27：S0 在可信 LAN、并发 1 的直接 Harbor 运行范围完成；S1 单 Trial Worker 主链、存储/提交恢复及中文任务页面已有验收证据。S2 Manifest、原始输出与上传恢复已实现，Viewer 支持轨迹搜索、安全媒体预览、有界内嵌/外部子轨迹按需读取和 64 MiB Manifest 文件下载。Hub/Web、PostgreSQL 与 S3 兼容对象存储已部署，真实 PostgreSQL 租约/Registry/API 集成测试通过；远端最近回归 38 passed、15 skipped。三类 Registry、审核运行绑定、确定性多 Trial 展开和单进程 FIFO 调度已实现；真实平台 Worker 多 Trial、双 Worker、持续网关路径与故障恢复仍未完成。目标数据库尚无 Registry revision、审核 Task archive 或业务 Job；加密备份/隔离读回也未验收。Job 多 Trial 评分统计已增加真实 PostgreSQL 验收；外部轨迹读取落实了 Manifest 精确路径、摘要校验及按需上限，真实 S3 集成仍需 MinIO 环境验收。正式执行入口保持关闭。当前进展及细化顺序见 [开发进展与后续计划](progress-and-next-plan.md)。

完整流程见 implementation-plan.md，运行命令见 spike.md，本地部署要求见 local-testing.md。高级 Pause/Resume/Fork 和 RL 继续后移。
