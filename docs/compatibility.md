# S0 兼容性与验收状态

**状态：两个真实小样例通过；完整 S0 尚未完成。** Hub 执行能力仍关闭。

| 组件 | 当前选择 | 状态 |
|---|---|---|
| Harbor | 0.24.0，Python 3.12+ | 发布 wheel 已下载、校验 SHA256 并检查源码 |
| E2B SDK | 2.46.0 | 实际依赖契约测试通过 |
| E2B Runtime | 固定提交的官方 Embed 测试部署 | Linux 部署、官方 smoke 与清理通过；受限内存 |
| Agent | Claude Code 2.1.81 | 官方 npm 包预装；实际执行与轨迹版本确认 |
| 模型 | DeepSeek V4.1 Flash，API ID deepseek-flash | 实际工具往返及两个 Harbor 任务通过 |

默认 API 地址为 https://api.deepseek.com/anthropic。其他服务商必须确认模型标识与 Anthropic Messages 兼容性，不能仅根据“OpenAI-Compatible”推断。

模型 ID 是服务商别名，可能随服务更新。记录调用日期、服务商模型版本说明与实际结果；别名本身不足以保证底层权重完全可复现。

## S0 必须完成的验收

- 固定 Harbor、SDK、Runtime 与 Agent 版本。
- 验证 Linux/KVM、API/数据路由、模板构建和清理。
- 两种代表性 Linux 单环境任务完成真实工具调用与 Verifier。
- 保存成功、零分、Agent 崩溃、Verifier 异常的原始证据。
- 明确轨迹、产物、日志、评分、usage 与 timing 的可用性；缺失值不能虚构。
- 验证凭证按用途隔离；公开示例任务不证明私密测试/Verifier Secret 隔离。
- 固定任务基础镜像 digest，记录 Runtime 提交、真实 Harness 版本及清理证据。

运行脚本使用 `await Trial.create(config)`、`await trial.run()`。Harbor 创建与销毁 Sandbox，平台不重复管理生命周期。

SDK 支持本地 E2B_API_URL/E2B_SANDBOX_URL。官方 Embed 导出这两个地址和 E2B_API_KEY，不应强制要求额外域名；API/数据路由需实际部署验证。

契约测试使用真实安装的依赖，不创建 Sandbox 或调用模型，不能算 Rollout E2E。运行步骤见 spike.md，本地部署见 local-testing.md。

参考：[DeepSeek Anthropic 接口](https://api-docs.deepseek.com/guides/anthropic_api/)、[E2B Embed 部署](https://github.com/e2b-dev/runtime/blob/main/embed/compose/README.md)。

本次结果与限制见 [真实验收记录](validation/2026-10-06-e2b.md)。
