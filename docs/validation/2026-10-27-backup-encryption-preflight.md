# 加密备份目标 fail-closed 验收

日期：2026-10-27。范围仅覆盖备份创建目标的加密挂载预检；没有创建备份或访问运行库。

## 实现

- Linux `create` 先确认父目录为私有目录且归 `rollforge` 所有，再以 `findmnt` 核实目标落在 `/mnt/rollforge-backups` 专用挂载点，并用 `lsblk` 检查设备依赖链包含 `crypt` 类型的 dm-crypt 层。
- 缺失挂载、根盘路径、非 dm-crypt 设备、工具不可用和无法解析的设备拓扑均拒绝备份。当前仅接受 dm-crypt/LUKS；目录 `0700` 与文件 `0600` 不能替代静态加密证明。

## 验证

- 单元测试模拟 dm-crypt、未加密设备和根盘挂载路径；另验证挂载目录所有权。备份专项 18 项通过。
- Linux 节点 `/opt/rollforge` 已快进到 `059fc81`。只读运行实际预检返回“备份目标不是已挂载的加密文件系统”，符合当前 `/mnt/rollforge-backups` 不是挂载点的状态；没有生成文件或访问 PostgreSQL/S3。
- 完整 `make check`：234 passed、54 skipped；前端类型检查与构建通过。`make runtime-check`：9 passed。

## 边界

本次没有可用的加密卷，因此真实加密设备链的主机验收尚未完成；单元测试中的 dm-crypt 拓扑由合成 `findmnt`/`lsblk` 输出验证。备份环境、独立只读 S3 身份、真实备份和隔离恢复仍待准备。执行门保持关闭。
