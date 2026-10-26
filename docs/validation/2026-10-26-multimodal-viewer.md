# ATIF 多模态附件元数据验收

日期：2026-10-26。针对固定 Harbor 0.24.0 的 ATIF v1.8 结构，Viewer 投影版本升级为 2，支持图片与音频内容部件，校验媒体类型、必填字段、音频时长及内容类型组合。音频常见 MIME 别名按 Harbor 模型归一化。新增内嵌子 Agent 轨迹树和 Observation 引用关联，限制每个文档最多 16 个直接子轨迹、总计 64 个子轨迹、最大嵌套深度 3、整个投影最多 5000 步；引用缺失或指向未内嵌文件时只返回未展开数量。

媒体路径只有与已接受 Execution Manifest 中的文件路径完全一致时才进入投影。ATIF 中的 URL、绝对主机路径和不存在的文件引用不会返回给浏览器，也不会触发服务端网络请求。外部子轨迹引用路径、会话 ID 不会暴露或自动加载。文件索引继续由现有所有权、Manifest 摘要和对象作用域检查保护。本切片只提供安全的附件类型/时长/路径元数据；尚未交付媒体字节预览或外部子轨迹读取。

兼容依据是 Harbor 官方固定标签 [v0.24.0 `pyproject.toml`](https://github.com/harbor-framework/harbor/blob/v0.24.0/pyproject.toml)、[ATIF 内容模型](https://github.com/harbor-framework/harbor/blob/v0.24.0/src/harbor/models/trajectories/content.py)、[Step 模型](https://github.com/harbor-framework/harbor/blob/v0.24.0/src/harbor/models/trajectories/step.py) 与 [ObservationResult 模型](https://github.com/harbor-framework/harbor/blob/v0.24.0/src/harbor/models/trajectories/observation_result.py)。实现继续使用受限白名单投影，不把 Harbor Runtime 引入 Hub。

验证：`tests/test_trajectory.py` 覆盖图片和音频元数据、音频 MIME 别名、Manifest 路径匹配、外部 URL 脱敏、无效 MIME 拒绝、时长字段、内嵌多层子轨迹/引用关联、重复/缺失 ID、深度、总轨迹数量和总步骤限制。`tests/integration/test_artifact_queries.py` 增加真实 PostgreSQL/MinIO API 断言，但本机未配置这些服务，该文件的 4 项测试在本轮 `make check` 中跳过；不能把它们记为真实对象存储验收。最终完整 `make check`：179 passed、51 skipped、1 项 Starlette/httpx 弃用警告；前端 OpenAPI 类型检查与生产构建通过。
