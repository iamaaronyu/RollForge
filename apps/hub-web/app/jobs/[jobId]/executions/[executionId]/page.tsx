import Link from "next/link";
import { notFound } from "next/navigation";
import { hub, hubText, HubProblem, message, resultText, validId, type ArtifactIndex } from "../../../../../lib/hub";

export const dynamic = "force-dynamic";
export default async function Artifacts({ params, searchParams }: {
  params: Promise<{ jobId: string; executionId: string }>;
  searchParams: Promise<{ file?: string }>;
}) {
  const { jobId, executionId } = await params;
  const { file } = await searchParams;
  if (!validId(jobId) || !validId(executionId)) notFound();
  const base = `/api/v1/jobs/${jobId}/executions/${executionId}`;
  const page = `/jobs/${jobId}/executions/${executionId}`;
  let index: ArtifactIndex;
  try { index = await hub<ArtifactIndex>(base + "/artifacts"); }
  catch (error) {
    if (error instanceof HubProblem && error.status === 404) notFound();
    return <main><Link href={`/jobs/${jobId}`}>← 执行历史</Link><h1>产物暂不可用</h1><p role="alert">{message(error)}</p></main>;
  }
  let content = ""; let problem = "";
  if (file) {
    const item = index.files.find(item => item.path === file);
    if (!item) notFound();
    if (item.size > 1024 * 1024) problem = "该文件超过 1 MiB，暂不支持文本预览。";
    else {
      try { content = await hubText(`${base}/artifact-text?path=${encodeURIComponent(file)}`); }
      catch (error) { problem = error instanceof HubProblem && error.status === 422 ? "该文件不支持文本预览。" : message(error); }
    }
  }
  return <main><header><Link href={`/jobs/${jobId}`}>← 执行历史</Link><span>执行产物</span></header>
    <h1>结果与原始记录</h1><p className="identifier">{executionId}</p><p className="score">{resultText(index.result)}</p>
    <section className="panel"><h2>文件索引</h2><div className="table-wrap"><table><thead><tr><th>文件</th><th>字节</th><th>预览</th></tr></thead><tbody>
      {index.files.map(item => <tr key={item.path}><td className="identifier">{item.path}</td><td>{item.size}</td><td>{item.size <= 1024 * 1024 ? <Link href={`${page}?file=${encodeURIComponent(item.path)}`}>打开</Link> : "超过预览上限"}</td></tr>)}
    </tbody></table></div><p className="note">仅展示已接受结果的文件。预览前核对内容摘要，不直接提供存储凭证或对象地址。</p></section>
    {file && <section className="panel"><h2 className="identifier">{file}</h2>{problem ? <p role="alert">{problem}</p> : <pre className="artifact-text">{content}</pre>}<p className="note">按原始 UTF-8 文本显示，最多 1 MiB。usage / reasoning 缺失时保持未知，不生成补值。</p></section>}
  </main>;
}
