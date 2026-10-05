# 贡献指南

先阅读 README 和 docs/roadmap.md。运行环境和共享 Schema 的较大改动先明确契约，贡献范围保持为一个可验收的功能切片。

运行 `make install`、`make check`。测试应验证关键不变量与错误路径；运行环境改动还需 `make runtime-check` 和真实执行证据，不能 Mock 整条 Harbor/E2B 链路后宣称集成完成。

文档默认中文。不要上传密钥、内部端点、数据集或原始用户轨迹。配置放在本地环境文件；领域契约统一放在 packages/schemas。

开源许可证尚未选择，贡献或复用代码前请确认仓库许可证状态。
