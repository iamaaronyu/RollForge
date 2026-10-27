# 外部子轨迹按需读取

日期：2026-10-27。

开发前只读检查了 Linux 固定 Runtime 中 Harbor 0.24.0 的 `harbor/models/trajectories/subagent_trajectory_ref.py`、`trajectory.py` 与 `observation_result.py`。实际 ATIF 契约规定，外部引用通过 `trajectory_path` 解析；`session_id` 仅供说明，不能用作查找键。实现没有将 Harbor Runtime 依赖引入 Hub。

Viewer 投影版本升至 3。主轨迹投影仅在外部 `trajectory_path` 与已接受 Execution Manifest 的相对文件路径精确相同时返回该路径；若 `trajectory_id` 已关联内嵌子轨迹，则优先使用内嵌版本。URL、主机路径、越界路径和清单外文件不返回，也不会被服务端请求。用户点击路径后，Hub 再次验证 USER 对执行的所有权、接受的 Manifest、精确文件路径和 SHA256/大小；文件必须为 JSON，单文件不得超过 1 MiB。Hub 以白名单投影返回轨迹，不回传 ATIF 自定义字段或 session ID。外部文件中指向其他 Manifest 文件的轨迹仍需用户逐项点击，不自动遍历。

测试覆盖投影对 Manifest 路径的筛选、URL/越界路径脱敏、摘要读取键、缺失文件、非 JSON、大文件和对象资源关闭。真实 PostgreSQL/MinIO API 集成用例另外覆盖用户归属、Worker 拒绝、轨迹返回、路径穿越、超限及对象篡改；只有提供固定 MinIO 二进制和隔离 PostgreSQL 时才运行。不能把本机跳过的 MinIO 用例计作真实对象存储验收。

本机 `make check`：**191 passed、54 skipped、1 warning**；OpenAPI 导出一致性、TypeScript 检查和 Next.js 生产构建通过。54 项跳过包含真实 PostgreSQL/MinIO 产物 API 测试，因此本切片的对象存储 API 集成断言尚未在真实 S3 兼容存储上运行；已通过的是适配器和对象读取边界单元测试。Starlette/httpx 弃用警告与本切片无关。

另在 `100.106.244.122` 完成当前代码的真实 API/对象存储闭环：一次性 PostgreSQL 16 容器和隔离 schema，当前提交源码临时导入，Hub API 通过 ASGI HTTP 调用，Silo 使用随机新建 bucket。**`POSTGRES16_SILO_EXTERNAL_API=PASS`**。检查了创建与接受合成执行产物、主轨迹仅返回 Manifest 内路径、外部 JSON 轨迹读回、其他用户 404、Worker 403、路径穿越/缺失路径 404、非 JSON 415、超过 1 MiB 为 413，以及对象篡改后为 503。测试结束删除了 S3 bucket、schema、PostgreSQL 容器和临时源码；没有更改运行 Hub/Web 或业务对象。

因此本功能已获得真实 PostgreSQL + Silo API 集成证据；`make check` 中的独立 MinIO fixture 仍因测试二进制未配置而跳过。包含本功能的 `d209366` 已部署到 Linux systemd Hub/Web；Hub readiness 与 Web 首页返回 200，Scheduler 与全部写入/执行门保持关闭。升级细节见[Linux 升级验收](2026-10-27-linux-upgrade.md)。
