# E2B 本地测试部署与准备要求

## 部署位置

开发控制面可以运行在 Mac；真实 E2B 放在同一局域网的专用 Linux 主机。Apple M1/M2 不适用官方 M3+ 嵌套虚拟化路径。启动 Docker Desktop 并不等于获得运行 Firecracker 所需的 KVM。

Linux 主机检查项：KVM/TUN 设备、4 KiB 内核页、cgroup v2；ARM64 还需内核 6.10+。本项目 x86_64 采用 6.8+ 内核测试基线。具体宿主与 Docker/Compose 要求以[官方固定版本指南](https://github.com/e2b-dev/runtime/blob/08436994a1c0bd48a7f03a08c020bf7a4a673dfa/embed/compose/README.md)为准。

建议为首次 1–2 并发验证准备 **4–8 vCPU、16 GiB 内存、60 GiB 空闲 SSD**。这是本项目留余量的建议，不是 20 并发容量承诺。SDK smoke、Harbor 模板构建和真实 Agent 分阶段验收。

## 检查宿主

在目标 Linux 主机运行项目脚本：

```sh
python3 scripts/check_e2b_host.py
```

该脚本只读取宿主信息，不安装软件、不修改内核或网络。通过不代表 E2B 已启动；它用于发现缺少 KVM、页大小/内核不合适或资源不足。

## 下载固定部署文件

部署目录建议与 RollForge 的 Hub Compose 分开。准备脚本仅下载固定提交的官方 Compose 与对应 .env，不修改宿主：

```sh
python3 scripts/prepare_e2b_embed.py --directory outputs/e2b-embed
```

文件默认保存在 Git 忽略的目录。下载完成后核对记录的 SHA256 和 Runtime 提交。不要随意只更新其中一个文件。

## 在专用 Linux 主机启动

```sh
cd outputs/e2b-embed
# 先确保脚本检查通过；初次使用官方 Compose 会配置宿主内核/网络。
docker compose up -d --wait
docker compose --profile test run --rm smoke
```

官方 Embed 需要宿主级权限，并调整内核设备、网络和运行目录，因此使用专用测试主机/VM。不要在当前 Mac 上执行这些启动命令，也不要把控制面 Compose 当成 Sandbox Runtime。

## Mac 到 Linux 的连接

建议 SSH 转发，避免直接开放运行时内部端口。以下 user/host 由实际测试机信息替换：

```sh
ssh -N \
  -L 3300:127.0.0.1:3000 \
  -L 3301:127.0.0.1:3001 \
  -L 3302:127.0.0.1:3002 \
  -L 5008:127.0.0.1:5008 user@host
```

模型调用还需要 Sandbox 能出站访问 DeepSeek API，模板构建需要访问镜像和依赖下载源。

从部署的 ready 服务导出 SDK 变量到本地受限文件，避免把 API Key 打印到聊天或 CI 输出。用其中的 Key 填写 .env.spike，并把本机 API/Sandbox 地址改为 3300/3302；模板上传还依赖 5008 转发。

```sh
umask 077
docker compose exec -T ready cat /run/e2b/sdk.env > sdk.local.env
```

## 停止与清理

普通 `docker compose down` 停止服务但保留数据，适合日常测试。删除卷和宿主清理会丢失模板、安装 Key 和数据，必须明确需要后再按官方指南执行；项目脚本不会自动清理这些内容。

## 目前需要 owner 提供的信息

1. 支持 KVM 的 Linux 主机位置、连接方式与可分配资源。
2. DeepSeek 官方 API Key 在本地完成配置；若用第三方平台，提供非敏感 endpoint 和精确模型 ID。
3. 初始建议仅测试两个公开样例任务、并发 1，稳定后再提高。
4. 原始轨迹与产物保持本地，不把测试数据自动发布到开源仓。

## 当前检查结论（2026-10-05）

已对 owner 指定的 Linux 测试机执行只读检查：Ubuntu 22.04、x86_64、内核 5.15、12 逻辑 CPU、7.6 GiB 内存、根盘剩余约 5.2 GiB。KVM/TUN、cgroup v2、Docker 28 和 Compose 2.32 可用。其余大分区是未挂载的 NTFS/FAT；不自动挂载、格式化或迁移 Docker 数据。

空间未达到官方 20 GiB 建议，内核未达到本项目基线；配置已传送到测试机，目标 Compose 2.32 配置校验通过；不启动完整栈。建议先准备足够的 Linux 文件系统空间并升级内核。8 GiB 内存属于资源受限实验，可降低 HugePages、仅运行一个 Sandbox，仍需实际验证稳定性。

当前 Mac 的 Compose 2.20 低于部署文件要求；Compose 配置验证应在目标 Linux 的 2.32 版本上执行。

owner 已选择继续准备现有主机，详细步骤见 [现有主机准备流程](host-preparation.md)。
