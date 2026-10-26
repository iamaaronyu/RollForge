# 受控产物下载验收

日期：2026-10-26。本切片增加单文件下载 API 与 Hub Web 同源代理。下载对象必须来自已接受 Execution 的 Manifest 精确相对路径；Hub 完整读取并校验摘要和字节数后，以固定 `application/octet-stream`、附件处置、`nosniff` 与 `no-store` 返回。对象上限为 64 MiB，超限返回 413。缺失路径及跨用户对象返回 404，Worker 身份被 USER 路由拒绝。文件名仅取 Manifest 相对路径的末级名称，并使用 RFC 5987 百分号编码。

网页仅向同源 API 路由发起下载。代理通过服务端 Hub token 访问 API，固定转发下载处置头，不将 token、S3 凭证、对象键或对象存储地址发给浏览器。代理及 Hub 请求不跟随重定向；缓存关闭。页面仅对不超过 64 MiB 的索引文件显示下载链接。

单元测试覆盖 Manifest 路径与摘要读取、UTF-8 文件名编码、64 MiB 上限和对象存储资源关闭。PostgreSQL/MinIO 集成测试新增成功下载、MIME/安全头、跨用户/Worker 拒绝、路径穿越拒绝与对象篡改拒绝断言。本机没有启用该测试所需的 PostgreSQL/MinIO，因此集成断言在本轮本机检查中跳过；这些断言不是当前环境的真实存储验收证据。完整 `make check`：185 passed、51 skipped；前端 API 类型检查、TypeScript 检查与生产构建通过。
