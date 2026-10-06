# S0 运行验证流程

首个组合：**Claude Code + DeepSeek V4.1 Flash + 自托管 E2B**。运行环境独立于控制面，依赖通过 integration/harbor-runtime/uv.lock 固定。

## 1. 安装并检查实际依赖契约

从仓库根目录运行：

```sh
make runtime-install
make runtime-check
cp -n .env.spike.example .env.spike
```

runtime-check 使用真实 Task、TrialConfig、SDK 配置模型，但不进行真实 Rollout。复制前确认 `.env.spike` 尚不存在，避免覆盖已有密钥。

## 2. 配置 Claude Code 和模型

在本地 `.env.spike` 中配置：

```dotenv
ROLLFORGE_SPIKE_AGENT=claude-code
ROLLFORGE_SPIKE_PROTOCOL=anthropic-messages
ROLLFORGE_SPIKE_MODEL=deepseek-flash
ROLLFORGE_SPIKE_MODEL_BASE_URL=https://api.deepseek.com/anthropic
ROLLFORGE_SPIKE_AGENT_VERSION=2.1.81
ROLLFORGE_SPIKE_TIMEOUT_SEC=300
ANTHROPIC_API_KEY=
```

把 DeepSeek API Key 填入 ANTHROPIC_API_KEY，不发到聊天或提交仓库。也支持 ANTHROPIC_AUTH_TOKEN，但 S0 建议统一使用 API_KEY，避免多种认证来源互相覆盖。

Harbor 将 endpoint 映射到 ANTHROPIC_BASE_URL，并把主模型、默认模型别名与 subagent 模型指向选定模型。测试阶段禁止切换到 Claude OAuth、Bedrock 或 Vertex；使用 API Key 模式确认实际请求到达目标服务。

现有框架还保留 Terminus 的 Chat Completions、Codex 的 Responses 接入能力，它们不是首个验收目标。替换 Harness 必须同时调整协议与版本。

模型凭证不写入 EnvironmentConfig.env；已安装 Harness 的认证由 Harbor 注入。Verifier 不配置模型凭证。公开样例不代表完成私密凭证隔离验收。

参考：[DeepSeek 官方 Anthropic 接口](https://api-docs.deepseek.com/guides/anthropic_api/)。

## 3. 配置自托管 E2B

先按 local-testing.md 准备 Linux/KVM 主机。若通过 SSH 隧道使用本机 3300/3302 转发端口：

```dotenv
E2B_API_URL=http://127.0.0.1:3300
E2B_SANDBOX_URL=http://127.0.0.1:3302
E2B_API_KEY=
```

域名型部署可配置 E2B_DOMAIN，并使用默认 https://api.<domain>；地址不能嵌入认证信息。E2B_API_KEY 是自托管安装生成的 Key，与 DeepSeek Key 无关。

控制面仍使用本机 3000/8000，避免与 E2B 官方默认 API 3000 端口冲突。先验证官方 Sandbox smoke，再验证 Harbor 模板构建与生命周期。

## 4. 离线预检

```sh
make spike
```

检查协议、固定版本、凭证是否存在、任务文件、内容 digest 和实际 Harbor 配置解析。配置缺失时返回 2，只报告字段/变量名，不打印值。`offline_preflight_passed` 不表示网络、模型或 Sandbox 可用。

## 5. 执行代表性任务

```sh
uv run --project integration/harbor-runtime python examples/run_one_trial.py --run
uv run --project integration/harbor-runtime python examples/run_one_trial.py \
  --task examples/tasks/coding-task --run
```

`--run` 会创建真实 Sandbox、执行 Agent、调用模型并验证结果；平台 S0 尚无自动重试；Harbor Provider 内部可能重试部分调用。超时会取消 Trial，但还需独立检查是否存在孤儿 Sandbox。

输出与 rollforge-evidence.json 保存在 Git 忽略的 outputs/spike 下，输出根目录权限强制为 0700。原始日志和产物可能包含敏感内容，只在本地审查。工具不会自动上传证据。

证据包含实际依赖版本、Task digest、原生输出 Manifest、版本化摘要。Manifest 在证据文件写入前计算，不包含证据文件自身。缺失 token/timing 保持 null。

- SCORED：已有有效评分，包括零分；不等于任务通过。
- RUNTIME_FAILED：出现运行异常，只打印异常类型，不打印原始错误消息。
- UNVERIFIED：没有评分。

## 6. 失败样本与最终验收

分别收集成功、零分、Agent 异常、Verifier 异常。检查失败阶段、部分日志、评分是否存在和 Sandbox 是否销毁。

示例 Dockerfile 已固定 Python 基础镜像 digest，由官方 registry 返回的内容摘要校验。记录 Runtime 提交、SDK/Agent 版本与服务商模型版本说明。

真实 E2B、模型、Agent、Verifier 和清理全部有证据后才完成 S0。S1 持久化与鉴权契约准备好之前，Hub 不开放执行入口。

## 失败模板的恢复

实测失败构建可能留下 alias，但没有可创建 Sandbox 的 default 标签。此时仅判断 alias_exists 不足以认定模板可用。修复资源或网络原因后，用运行入口的 `--force-build` 调用 Harbor 原生重建配置，不删除已有模板。

当前公开小样例请求 512 MiB Sandbox 内存，用于 8 GiB 主机上的并发 1 实验；模板构建可能同时保留多个 VM。该配置不代表一般代码任务的资源建议，正式任务需单独配置内存并验证。

当前样例 Dockerfile 通过官方 npm 包预装 Claude Code 2.1.81，Harbor 会检查版本后复用；没有绕过 Harness。原因是测试网无法连接原生 bootstrap 域名，而 npm 固定包可达。部署限额和已有模板缓存处理见 local-testing.md。

## 真实故障验收

在独占、无活动 Sandbox 的自托管测试环境执行：

```bash
UV_CACHE_DIR=.cache/uv uv run --project integration/harbor-runtime python examples/run_fault_acceptance.py --run
```

脚本顺序验证有效零分、Verifier 错误与超时、Agent 超时与 CLI 错误、Verifier 阶段取消及恢复时重复取消。它会调用模型 API，并在每项后检查剩余 Sandbox；清理失败后停止创建新 Sandbox，不接管或删除其他运行。未传 --run 时不执行。原始输出和证据位于 Git 忽略的 outputs/spike/faults-*，仅非敏感摘要可公开。

2026-10-06 六项真实验收通过，详细故障注入与边界见 [验收记录](validation/2026-10-06-e2b.md)。
