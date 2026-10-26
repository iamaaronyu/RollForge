# Linux 独立端口、Web 服务与部署预检

日期：2026-10-26。本切片为 RollForge 数据服务配置可覆盖的 loopback 主机端口，并增加本机访问的 Web systemd unit 和启动前预检。没有连接模型或启动远端服务。

Compose 默认映射 PostgreSQL、Redis、MinIO API 和 MinIO Console 至 `127.0.0.1:55432`、`:56379`、`:59000`、`:59001`，避免 E2B 常用的 `5432`、`6379`、`9000`、`9001`。端口可通过 `ROLLFORGE_*_HOST_PORT` 覆盖。检查目标节点时发现 Web 常见端口 3000 已占用，而 3300、Hub 的 8000 和四个数据服务端口均空闲；因此 Web 的本机端口由受保护 `web.env` 的 `ROLLFORGE_WEB_HOST_PORT` 配置，初始建议 3300。PostgreSQL 与 MinIO 继续使用独立命名卷，不依赖 E2B 的状态。

新增 `rollforge-web.service`，使用系统 Node.js、`rollforge` 账号、严格 systemd 文件系统保护，只绑定 `127.0.0.1`；监听端口从 root 管理的 `/etc/rollforge/web.env` 读取（目标节点建议 3300）。单用户 UI 没有登录系统，部署手册要求只经 SSH 隧道访问，不接公网反向代理。

`scripts/check_deployment.py` 在启动前静态校验敏感环境文件权限、数据库与存储强凭证、Hub USER/WORKER 凭证摘要、Web USER Token 归属、受限服务环境变量和凭证隔离、loopback URL 与 unit 加固选项、首次部署写入/Worker/Scheduler/Web 开关均关闭、Compose 配置、端口可用性和 PostgreSQL/MinIO 命名卷。预检将 Compose 输出和配置值留在内存，不打印凭证；它不启动、停止或修改容器，也不代表服务健康或真实执行验收通过。

验证：`docker compose config --format json` 展开的四个数据服务端口均绑定 loopback、互不冲突，并避开 E2B 常用端口。13 项预检单元测试覆盖凭证隔离、网关 HTTPS/LAN HTTP 策略、所有执行门保持关闭、端口冲突/公开绑定、权限和持久卷。完整 `make check`：155 passed、51 skipped；Python lint/格式、OpenAPI 检查、Web 类型检查与 Next.js 构建通过；有一项既有 Starlette/httpx 弃用警告。`make runtime-check`：9 passed。目标 Ubuntu 上的 `systemd-analyze verify` 对 Web unit 临时副本返回成功；仅将 ExecStart、EnvironmentFile 与可写缓存目录替换为本机安全占位路径，验证 parser 和 unit 指令，不安装服务。2026-10-26 对目标机只读复核确认 `sudo -n true` 不可用、用户级 systemd 可用且 linger 已开启；E2B 占用 `5432/6379/9000`，RollForge 配置端口及 Hub/Web 端口空闲。本次未使用 Docker 或改动远端文件。目标机既有 unit 的权限/兼容性警告与 RollForge 无关。

边界：本轮未在 `100.106.244.122` 安装文件或启动服务。目标机的管理员安装、独立 Compose 健康检查、Hub readiness、数据库备份读回、对象存储验收、TLS/网关部署认证及真实 Harbor/E2B/模型主链仍待完成。若首次部署预检缺少上述私密配置，会安全失败；不要为通过检查而开放执行开关或复用 E2B 数据卷。

## 加固补充

后续预检增加独立 `start_infrastructure.py` 启动入口。`make infra` 现在必须显式传入 0600 `ROLLFORGE_INFRA_ENV`，不再默认使用仓库 `.env`；Compose 的 PostgreSQL/MinIO 凭证也没有开发口令兜底。启动前验证随机强凭证、口令彼此不同、数据端口不与 E2B 默认端口重叠、实际端口空闲、绑定 loopback、容器端口目标正确、持久卷独立。Compose 读取受保护文件时会清除同名 shell 环境变量，避免 shell 值覆盖文件值。

完整部署预检还核对 Hub PostgreSQL URL 的 loopback 地址、端口、数据库名、用户名和口令与专用 `infra.env` 一致；MinIO endpoint 必须使用独立 loopback API 端口，Redis（若配置）必须使用其独立 loopback 端口。新增拒绝默认 E2B 端口、错误容器端口、弱凭证、shell 污染和 Hub 指向 E2B/其他实例的回归测试。预检测试 19 passed；完整 `make check` 为 169 passed、51 skipped；`make runtime-check` 为 9 passed。

本次仅验证代码和静态配置；本机 Docker daemon 不可用，未运行 Compose，也未对服务器安装、启动或修改任何服务。Linux 上的数据服务健康、Hub readiness、真实对象存储/凭证/网关验证和备份恢复读回仍需在执行开关保持关闭时完成。
