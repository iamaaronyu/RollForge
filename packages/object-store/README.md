# 执行输出对象存储

共享契约在 packages/schemas/storage.py；可运行 Task/Agent/Model 绑定在 packages/schemas/runnable.py。本包不管理 Sandbox，也不改变数据库执行权。

## Worker 接口

- `S3ObjectStore(endpoint, bucket, access_key, secret_key, allow_local_http=False)`：凭证使用 SecretStr。HTTP 只在显式选择本机测试且地址为 loopback 时允许。
- `BundlePublisher(store, sensitive_values=(...))`：传入本次执行可能出现的凭证值，包括模型会话、E2B 与对象存储凭证。发布前对所有文件做预检，命中时整个输出不上传；这不是通用 DLP。
- `prepare(scope, native_output_dir)`：输出 ArtifactManifest，不上传。
- `publish(scope, native_output_dir)`：文件全部条件写入并读回校验后，最后写 Manifest；返回可提交的 ResultCommit。
- `verify(scope, manifest_digest)`：只依赖远端对象，核验完整 Manifest 和文件摘要，从原始 result.json 重新解析汇总，返回相同 ResultCommit。
- `S3ObjectStore.close()`：调用方结束时关闭 SDK 连接。

`scope` 是 ExecutionScope，包含 job_id、trial_id、execution_id、fencing_token。相同 Execution 的输出变更会被拒绝；Retry 必须使用新 Execution。对象键为 `jobs/{job_id}/trials/{trial_id}/executions/{execution_id}/files/{relative_path}`，Manifest 位于同一前缀下的 manifest.json。

同步方法会访问网络；异步 Worker/API 应使用 asyncio.to_thread 并保持租约续租。API 应先通过 verify 验证远端内容，再在 PostgreSQL 事务中校验当前租约与 fencing、完成提交；不能以存储对象存在代替有效执行权。

底层采用 [官方 S3 PutObject 的 IfNoneMatch 条件](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)：仅写入不存在的对象。已有对象须完整读回、内容相同才允许重放；409 并发冲突返回安全的可重试错误，不降级为无条件覆盖。SHA256 来自实际内容，不信任 HEAD 或调用方写入的摘要元数据。

## 恢复语义

上传期间进程中断或写入响应丢失：保留本地原始输出，用相同 scope 重放 publish。已有对象只在内容完全相同时复用；任何冲突都拒绝覆盖。Manifest 尚未发布的部分对象不算完成。

Manifest 上传后、数据库提交前中断：保存非敏感的 scope 和 Manifest 摘要，用 verify 从远端恢复 ResultCommit；可在有效租约内重放完成提交。租约失效后旧执行必须被数据库拒绝，由调度器回收、新 Execution 重试。存储包不会自行续租、抢占或写 PostgreSQL。

当前每文件上限 64 MiB、每 Execution 总大小上限 128 MiB、最多 512 个文件。不提供 multipart、大对象断点续传、对象垃圾回收、STS 或按 Execution 发放临时存储权限。生产存储策略仍须禁止无条件覆盖、删除和跨租户访问；本机验收使用随机测试管理员凭证，不代表生产授权已验收。
