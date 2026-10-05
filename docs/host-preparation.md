# 现有 Linux 测试机准备流程

## 初次检查结论（2026-10-05）

已完成只读检查和官方 Compose 语法验证。主机有 KVM/TUN、cgroup v2，Docker/Compose 可用；CPU 足够首轮并发 1。部署暂未启动，因为 Linux 根文件系统剩余约 5.2 GiB，内存约 7.6 GiB，运行内核为 5.15。

## 1. 准备存储

先使 Linux ext4 文件系统至少有 20 GiB 空闲；建议预留 60 GiB。现有 Docker 镜像约 4.7 GB、构建缓存约 0.8 GB、用户缓存约 3.4 GB，即使全部清理也不足以达到 20 GiB。环境/工作目录与 CUDA 占用不能当作可随意删除的缓存。

优先方案是扩容 Linux 文件系统或接入新的 ext4 SSD。其他现有大分区包含 NTFS/FAT，不能仅凭未挂载就认为没有数据。分区、格式化和数据迁移由 owner 确认设备与备份后操作，不自动执行。

如果选择迁移 Docker 数据目录，先完成存储挂载，再停 Docker、复制并验证数据、更新 daemon.json 中的 data-root、重启并检查。目标需要 Linux 文件系统，并有可靠的开机挂载；路径未确定前不生成或执行迁移命令。

```sh
df -hT /
docker system df
```

## 2. 安装 Ubuntu 22.04 HWE 内核

仓库源已确认提供 linux-generic-hwe-22.04 6.8 候选版本，符合项目 x86_64 测试基线。参见 [Ubuntu 官方内核生命周期说明](https://ubuntu.com/kernel/lifecycle)。

当前远程账号不能免密码 sudo。owner 在主机终端运行以下命令并本地完成认证；不发送密码：

```sh
sudo apt-get update
sudo apt-get install --install-recommends linux-generic-hwe-22.04
sudo apt-get install iptables rsync e2fsprogs iproute2 python3-venv
```

安装完成后，由 owner 选择维护时间重启。保留旧内核作为启动回退选项；CUDA/GPU 等已有用途需要一起确认新内核兼容性。

```sh
sudo reboot
# 重新连接后确认：
uname -r
ls -l /dev/kvm /dev/net/tun
```

## 3. 受限内存配置

完整 Embed 官方建议至少 12 GiB 内存，本项目建议 16 GiB。若仍使用现有 8 GiB，先以并发 1 做实验，将部署 .env 中 HUGEPAGES 从 2048 调为 1024，即保留约 2 GiB 大页池。512 页不足以容纳 2 GiB Sandbox；只降低到 1024，不声称一定能稳定运行完整栈。

HugePages、ClickHouse、Postgres、模板构建和 Agent 安装共同使用宿主资源。部署前检查可用内存，启动后观察实际 RSS、OOM 和大页余量。若无法稳定启动或创建 Sandbox，应增加物理内存，而不是无限重试。

## 4. 部署与验收顺序

1. owner 准备空间、安装内核并重启，告知完成。
2. 重新运行 scripts/check_e2b_host.py；内存建议不满足时记录受限实验条件。
3. 确认端口没有冲突，检查固定提交和文件 SHA256。
4. 启动 Embed，验证服务健康和官方 SDK smoke。
5. 导出 E2B Key 至受限本地文件，建立 SSH 隧道；不打印 Key。
6. 跑 Claude Code + DeepSeek 的两个样例任务；检查评分、原始输出与 Sandbox 清理。
7. 成功与失败样本完成后更新 S0 验收记录，再继续 Hub 持久化执行能力。

部署配置已放在测试机用户目录的 `rollforge-e2b/08436994a1c0bd48a7f03a08c020bf7a4a673dfa/`，里面没有模型密钥。普通停止保留数据；不自动删除卷、镜像或分区。

## 2026-10-06 复检

owner 已完成空间和内核准备：内核 6.8.0-138、根文件系统 ext4，部署前空闲约 410 GiB。内存仍为 7.57 GiB，本次显式采用并发 1、HUGEPAGES=1024 的受限实验。宿主初始化与数据库迁移已通过。

部署实测：2 GiB VM 的模板快照恢复超出受限大页池；3 GiB 大页池在线预留失败。当前小样例改为 512 MiB VM、2 GiB 大页池，仅用于链路验证。正式任务需增加主机内存或重新配置可预留的大页容量，不能仅用 Worker 并发数估算峰值 VM 数。
