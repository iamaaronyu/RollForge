import { hubArtifactDownload, HubProblem, validId } from "../../../../../../../lib/hub";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024;

export async function GET(request: Request, { params }: {
  params: Promise<{ jobId: string; executionId: string }>;
}) {
  const { jobId, executionId } = await params;
  const path = new URL(request.url).searchParams.get("path");
  if (!validId(jobId) || !validId(executionId) || !path || path.length > 768) {
    return new Response("请求的产物无效。", { status: 400, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  try {
    const upstream = await hubArtifactDownload(
      `/api/v1/jobs/${jobId}/executions/${executionId}/artifact-download?path=${encodeURIComponent(path)}`,
    );
    const bytes = await upstream.arrayBuffer();
    if (bytes.byteLength > MAX_DOWNLOAD_BYTES) return new Response("产物超过 64 MiB 下载上限。", { status: 413 });
    const headers = new Headers({
      "Content-Type": "application/octet-stream",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'",
    });
    const disposition = upstream.headers.get("content-disposition");
    if (disposition?.startsWith("attachment; filename*=")) headers.set("Content-Disposition", disposition);
    return new Response(bytes, { status: 200, headers });
  } catch (error) {
    const status = error instanceof HubProblem ? error.status : 502;
    return new Response(status === 404 ? "产物不存在或无权访问。" : "产物下载失败。", {
      status,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
      },
    });
  }
}
