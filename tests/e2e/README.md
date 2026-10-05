# 真实执行验收

Mock 的 Harbor/E2B 测试不算真实 E2E。S0 必须固定版本，在自托管 Sandbox 中运行 Claude Code，调用已授权的 DeepSeek API，检查真实轨迹、产物、日志、评分与清理。

此目录没有伪装成通过的占位测试。离线依赖契约测试位于 integration/harbor-runtime/tests，不属于真实 Rollout 验收。当前状态见 docs/compatibility.md。
