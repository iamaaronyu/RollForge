# RollForge

开源 Agent Rollout 与评测平台。复用 Harbor 执行任务、E2B 提供 Sandbox，自研实验控制面与轨迹、产物、评分数据层。

**当前状态：S0 基础真实链路已验收，完整 S0 关卡仍在进行。** 已提供健康接口、共享 Schema、状态机、前端基础页，以及独立的 Harbor/E2B 运行环境、预检和结果解析。Hub 的 Job 执行、持久化租约、Registry 与生产结果存储尚未实现。

首个验证组合为 **Claude Code + DeepSeek V4.1 Flash + 自托管 E2B**。默认使用 DeepSeek 官方 Anthropic 兼容接口；其他服务商必须单独确认协议与模型标识。

两个公开样例已通过真实 Claude Code + DeepSeek + E2B 执行，评分均为 1.0；详见 [2026-10-06 验收记录](docs/validation/2026-10-06-e2b.md)。

## 快速启动

控制面需要 Python 3.11+、uv、Node.js 20.9+、npm 和 Docker Compose。独立 Harbor 0.24.0 运行环境需要 Python 3.12+。

```sh
cp .env.example .env
uv sync --all-packages --locked
npm --prefix apps/hub-web ci
make infra
make api
# 在另一个终端运行：
make web
```

API 文档：http://localhost:8000/docs；网页：http://localhost:3000。

```sh
make check
make worker-check
make scheduler-check
uv run alembic -c apps/hub-api/alembic.ini current
```

迁移环境已接通，业务表将在执行契约确认后实现。Worker/Scheduler 目前仅支持 `--check`，不会模拟真实 Job 执行。

## S0 真实链路准备

```sh
make runtime-install
make runtime-check
cp -n .env.spike.example .env.spike
# 在本地配置模型密钥和 E2B 接入信息后：
make spike
```

`make spike` 默认只做离线预检。真正执行需要显式 `--run`，参见 [S0 验证流程](docs/spike.md)。

E2B Runtime 需要 Linux/KVM，不能直接作为普通 macOS 容器运行。Apple M1/M2 不适用官方 M3+ 嵌套虚拟化路径，建议使用局域网 Linux 测试机，详见 [本地测试部署](docs/local-testing.md)。

## 目录结构

```text
apps/hub-api              FastAPI、SQLAlchemy、Alembic
apps/hub-web              Next.js、TypeScript
services/worker           执行进程入口
services/scheduler        调度进程入口
packages/schemas          统一领域契约
packages/common           环境配置
packages/harbor-adapter    Harbor 集成边界
packages/sandbox-provider Sandbox 配置边界
packages/hub-sdk           类型化 API 客户端
integration/harbor-runtime 独立的固定版本运行环境
examples/                 单 Trial 入口与示例任务
infra/                    基础设施与部署说明
tests/                    单元、集成与真实 E2E 验收要求
```

文档入口：[架构](docs/architecture.md)、[开发规范](docs/development.md)、[API](docs/api.md)、[兼容性](docs/compatibility.md)、[实施计划](docs/implementation-plan.md)、[路线图](docs/roadmap.md)。

开源许可证尚未确定；仓库公开不等于已授予软件使用许可。
