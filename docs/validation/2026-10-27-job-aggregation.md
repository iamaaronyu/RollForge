# Job 多 Trial 评分汇总验收

日期：2026-10-27。

Job 详情新增评分汇总：终态完成/失败/取消数、已评分 Trial 数，以及每个 reward 指标的观测数、均值、最小值和最大值。统计查询先验证 USER 对 Job 的所有权，并只连接每个 Trial 当前 fencing token 对应的 Execution 结果；物理 Retry 不增加逻辑样本。零分是有效评分，缺失指标不补零；未评分结果不会进入 reward 分布。共享契约由 Hub API、Python SDK、OpenAPI 和中文详情页共同使用。

真实 PostgreSQL 集成用例包括三个逻辑 Trial：首个租约过期后重新排队并 Retry，随后两个 Trial 得分（含零分及只出现在一个样本中的 `quality` 指标），第三个 Trial 以 Agent 错误结束。断言验证 3 个逻辑样本、2 个有效评分、各状态计数、`reward` 覆盖数 2/均值 0.4/最小值 0/最大值 0.8、`quality` 覆盖数 1，以及其他 USER 和 WORKER 的访问分别被拒绝。Retry 的 FIFO 位置由队列实际返回顺序处理，不假设它排在新样本之前。

验收在 `100.106.244.122` 上完成：使用已有缓存的 PostgreSQL 16 镜像，启动临时数据库容器并只绑定 `127.0.0.1` 动态端口；测试使用空数据库和随机 schema。源码与测试只放在临时目录，测试后自动删除容器、schema 和文件，未连接或修改 RollForge 运行数据库/服务。新增 PostgreSQL 用例 **1 passed**。

本机 `make check`：**185 passed、53 skipped、1 warning**；跳过项包含本机未配置 PostgreSQL/MinIO 的集成测试。API 类型生成、OpenAPI 一致性、TypeScript 检查和 Next.js 生产构建通过。Starlette/httpx 弃用警告与本切片无关。
