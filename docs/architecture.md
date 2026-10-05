# 架构说明

Portal → Hub API → Job/Trial/Execution → Scheduler → Worker → Harbor → E2B → Agent → 模型 API → Verifier → 对象存储与 Hub 元数据。

Hub 首先采用模块化应用。PostgreSQL 管理状态与租约，Redis 用于通知和缓存，故障后应能从 PostgreSQL 恢复。Harbor 管理 Sandbox 生命周期，Provider 提供配置与能力描述，不在 Harbor 外重复创建 Sandbox。

Job 内的逻辑 Trial 由 Task Revision × Agent Revision × Model Revision × Attempt 确定。Retry 创建新 Execution，不增加评测样本数。Execution 回调必须携带 fencing token，只有当前执行者可最终提交。现有状态机辅助函数仅负责合法性校验，尚未实现分布式租约。

保存版本化的原始输出，再生成 Viewer 投影。缺失的 usage/reasoning 表示未知；零分是有效评分，不能归类为基础设施失败。指标分别展示通过率与评分覆盖率。

首个验证 Agent 为 Claude Code，模型选择 DeepSeek V4.1 Flash，使用官方 Anthropic 兼容 API。控制面与独立运行环境分别锁定依赖，不把 Harbor 和模型 SDK 全部引入 API 进程。

自托管部署、真实工具调用、模型协议及资源清理仍需环境证据。完成 S0 前不启用 Hub 执行入口。
