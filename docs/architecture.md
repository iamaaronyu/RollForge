# 架构说明

Portal → Hub API → Job/Trial/Execution → Scheduler → Worker → Harbor → E2B → Agent → 模型 API → Verifier → 对象存储与 Hub 元数据。

Hub 首先采用模块化应用。PostgreSQL 管理状态与租约，Redis 用于通知和缓存，故障后应能从 PostgreSQL 恢复。Harbor 管理 Sandbox 生命周期，Provider 提供配置与能力描述，不在 Harbor 外重复创建 Sandbox。

Job 冻结一个 ExecutionSnapshot，并可确定性展开 1–1000 个逻辑 Trial；attempt_index 标识同配置下的独立样本序号。Retry 保留 Trial，创建新 Execution，不增加评测样本数。Execution 具有 Job 内单调领取序号用于稳定历史分页，并携带 fencing token，只有当前执行者可最终提交。PostgreSQL 事务租约服务覆盖领取、续租、幂等提交和有界过期回收。持续调度进程以有限并发轮询 FIFO Trial 队列，通过 Worker 鉴权 API 回收过期租约；进程内容量明确受并发配置限制。单 Trial 真实主链已在受限 LAN、并发 1 验收；多 Trial 真实主链、双 Worker Harbor/E2B 运行和跨进程全局容量仍待验证。

USER 可取消单个 Trial 或整个 Job。取消在 PostgreSQL 事务中锁定 Trial 与当前 Execution，并按状态机将其置为 CANCELLED；完成、续租与取消并发时由相同 Trial 锁确定唯一终态。Worker 在下次续租时发现租约撤销，终止 Runtime 并等待 Harbor 清理；真实 Sandbox 取消时延和强制中断恢复仍待验收。

保存版本化的原始输出，再生成 Viewer 投影。缺失的 usage/reasoning 表示未知；零分是有效评分，不能归类为基础设施失败。指标分别展示通过率与评分覆盖率。

Registry Job API 校验用户所有权、revision 摘要和部署者审核的精确运行组合；快照同时保留注册规范摘要与 Worker 使用的 Task 内容/运行绑定摘要。直接 Job API 不允许传入可运行 runtime。

首个验证 Agent 为 Claude Code，模型选择 DeepSeek V4.1 Flash，使用官方 Anthropic 兼容 API。控制面与独立运行环境分别锁定依赖，不把 Harbor 和模型 SDK 全部引入 API 进程。

S0 自托管部署与隔离、S1 单 Trial Worker 主链已有真实环境证据。网关使用 SQLite 持久化请求次数与基于供应商 Token 用量的保守会话费用预算；真实模型账单对账、TLS、部署身份认证仍未验收。Hub 执行入口保持关闭，未知任务和多租户隔离尚未验收。
