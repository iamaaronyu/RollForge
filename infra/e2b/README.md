# 自托管 E2B 边界

E2B 使用独立 Linux/KVM 宿主；本项目 Hub 的 Compose 不模拟 E2B。Apple M1/M2 不适用官方 M3+ 嵌套虚拟化路径。

本地测试部署步骤见 [local-testing.md](../../docs/local-testing.md)。准备脚本下载固定提交的官方部署文件，宿主检查脚本仅执行只读诊断。

先验证 API、Sandbox 数据路由、模板构建、模型出站网络和清理，再做真实 Harbor 验收。SDK 锁定版本不代表已验证所有 Runtime 组合。

密钥、内部主机名和生产配置只保存在本地，不提交仓库。
