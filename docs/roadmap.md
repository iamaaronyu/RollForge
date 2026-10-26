# 开发路线图

1. **S0**：Claude Code + DeepSeek V4.1 Flash + 自托管 E2B 真实链路。
2. **S1**：单任务 Job、Trial/Execution 持久化、事务租约、fencing、幂等提交与有界恢复。
3. **S2**：版本化结果 Manifest、原始输出、上传恢复与 Trial 页面。
4. **S3**：不可变 Task/Dataset Revision、Registry、FIFO/容量调度与 Job 页面；从 1/2 并发逐步验证到 100 个逻辑 Trial。
5. **S4**：取消与清理、故障测试、监控、部署与运行手册，完成 MVP 验收。
6. 后续：企业治理、实验比较、数据挖掘和训练导出。

2026-10-26：S0 在可信 LAN、并发 1 范围完成；S1 单 Trial Worker 主链、存储/提交恢复及最小中文任务页面已有验收证据。S2 Manifest、原始输出与上传恢复已实现，基础 Viewer/主轨迹时间线和浏览器内轨迹文本搜索已实现。三类 Registry、受审核目录约束的 Registry 运行绑定、资产版本网页、Job 内确定性多 Trial 展开和单进程 FIFO 调度已实现；真实多 Trial/双 Worker Harbor 验收、进程故障恢复仍待完成。大文件下载、多模态/子轨迹展开及统计聚合仍待开发。Linux 部署补充了 E2B 避让端口、Web loopback systemd unit 和密钥静默预检；目标主机安装仍待管理员配置与真实验收。正式执行入口保持关闭。当前进展及细化顺序见 [开发进展与后续计划](progress-and-next-plan.md)。

完整流程见 implementation-plan.md，运行命令见 spike.md，本地部署要求见 local-testing.md。高级 Pause/Resume/Fork 和 RL 继续后移。
