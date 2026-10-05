# 开发路线图

1. **S0**：Claude Code + DeepSeek V4.1 Flash + 自托管 E2B 真实链路。
2. **S1**：单任务 Job、Trial/Execution 持久化、事务租约、fencing、幂等提交与有界恢复。
3. **S2**：版本化结果 Manifest、原始输出、上传恢复与 Trial 页面。
4. **S3**：不可变 Task/Dataset Revision、Registry、FIFO/容量调度与 Job 页面；从 1/2 并发逐步验证到 100 个逻辑 Trial。
5. **S4**：取消与清理、故障测试、监控、部署与运行手册，完成 MVP 验收。
6. 后续：企业治理、实验比较、数据挖掘和训练导出。

S0 工具代码已经实现：独立运行环境、配置预检、两个示例任务、原生运行入口和结果解析。真实执行验收仍待模型凭证与 Linux/KVM 部署。

完整流程见 implementation-plan.md，运行命令见 spike.md，本地部署要求见 local-testing.md。高级 Pause/Resume/Fork 和 RL 继续后移。
