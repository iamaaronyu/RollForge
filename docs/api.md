# API 说明

| 接口 | 含义 |
|---|---|
| `GET /health/live` | 进程存活，不依赖外部服务 |
| `GET /health/ready` | 检查数据库连通性，不可用时返回 503 |
| `GET /api/v1/platform` | 版本、开发阶段与执行能力状态 |
| `GET /openapi.json` | 自动生成的 API 契约 |
| `GET /docs` | 交互式 API 文档 |

尚未开放 Job/Trial 写接口。真实执行入口需要不可变配置快照、用户/Worker 鉴权、事务租约、fencing、幂等提交与持久化执行状态。

健康响应不包含连接字符串或原始基础设施错误。当前 API 尚未依赖 Redis 和对象存储，因此未将其纳入就绪检查。

开发 API 绑定本机回环地址；完成鉴权与生产部署加固后再向外提供服务。
