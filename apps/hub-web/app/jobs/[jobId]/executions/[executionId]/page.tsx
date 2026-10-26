import Link from "next/link";
import { notFound } from "next/navigation";
import { hub, hubMedia, hubText, HubProblem, message, resultText, validId, type ArtifactIndex, type TrajectoryView } from "../../../../../lib/hub";
import { TrajectoryViewer } from "./trajectory-viewer";

export const dynamic = "force-dynamic";
type Subtrajectory = TrajectoryView["subagents"][number];

function mediaReferences(trajectory: TrajectoryView): TrajectoryView["steps"][number]["media"][number][] {
  const result: TrajectoryView["steps"][number]["media"][number][] = [];
  const steps = (items: readonly TrajectoryView["steps"][number][]) => {
    for (const step of items) result.push(...step.media);
  };
  const children = (items: readonly Subtrajectory[]) => {
    for (const child of items) {
      steps(child.steps);
      children(child.subagents);
    }
  };
  steps(trajectory.steps);
  children(trajectory.subagents);
  return result;
}

export default async function Artifacts({ params, searchParams }: {
  params: Promise<{ jobId: string; executionId: string }>;
  searchParams: Promise<{ file?: string; media?: string }>;
}) {
  const { jobId, executionId } = await params;
  const { file, media } = await searchParams;
  if (!validId(jobId) || !validId(executionId)) notFound();
  const base = `/api/v1/jobs/${jobId}/executions/${executionId}`;
  const page = `/jobs/${jobId}/executions/${executionId}`;
  let index: ArtifactIndex;
  try { index = await hub<ArtifactIndex>(base + "/artifacts"); }
  catch (error) {
    if (error instanceof HubProblem && error.status === 404) notFound();
    return <main><Link href={`/jobs/${jobId}`}>← 执行历史</Link><h1>产物暂不可用</h1><p role="alert">{message(error)}</p></main>;
  }
  let trajectory: TrajectoryView | undefined; let trajectoryProblem = "";
  if (index.files.some(item => item.path === "agent/trajectory.json")) {
    try { trajectory = await hub<TrajectoryView>(base + "/trajectory"); }
    catch (error) { trajectoryProblem = error instanceof HubProblem && [413, 422].includes(error.status) ? "轨迹超出投影范围或格式不受支持，可查看原始文件。" : message(error); }
  }
  let mediaPreview: { dataUrl: string; mediaType: string } | undefined;
  let mediaProblem = "";
  if (media) {
    if (!trajectory || !mediaReferences(trajectory).some(item => item.artifact_path === media)) notFound();
    try { mediaPreview = await hubMedia(`${base}/artifact-media?path=${encodeURIComponent(media)}`); }
    catch (error) { mediaProblem = error instanceof HubProblem && error.status === 413 ? "该媒体超过 8 MiB 预览限制。" : error instanceof HubProblem && error.status === 415 ? "该文件类型不支持媒体预览。" : message(error); }
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
    <section className="panel"><h2>执行时间线</h2>
      <p className="note">主轨迹保留原始顺序，搜索覆盖主轨迹最多 1000 步的消息、推理、工具调用、返回和媒体元数据。已内嵌子轨迹可展开；外部子轨迹不自动取址。用量不累加，复制上下文单独标记，缺失字段显示未知。图片和音频显示受 Manifest 确认的附件路径与类型，可预览不超过 8 MiB 的附件。</p>
      {trajectoryProblem && <p role="alert">{trajectoryProblem}</p>}
      {!trajectory && !trajectoryProblem && <p>尚无可展示的主轨迹。</p>}
      {trajectory && <><p>来源 {trajectory.source_version} · 投影版本 {trajectory.projection_version}</p>
        <TrajectoryViewer trajectory={trajectory} />
      </>}
    </section>
    {media && <section className="panel"><h2 className="identifier">{media}</h2>{mediaProblem ? <p role="alert">{mediaProblem}</p> : mediaPreview?.mediaType.startsWith("image/") ? <img src={mediaPreview.dataUrl} alt={`执行附件 ${media}`} style={{ maxWidth: "100%", maxHeight: "70vh", objectFit: "contain" }} /> : mediaPreview && <audio controls preload="none" src={mediaPreview.dataUrl}>浏览器不支持音频播放。</audio>}<p className="note">媒体由 Hub 按执行所有权、Manifest 清单、内容摘要和 8 MiB 上限验证后提供；网页不会获得对象存储凭证。</p></section>}
    <section className="panel"><h2>文件索引</h2><div className="table-wrap"><table><thead><tr><th>文件</th><th>字节</th><th>预览</th></tr></thead><tbody>
      {index.files.map(item => <tr key={item.path}><td className="identifier">{item.path}</td><td>{item.size}</td><td>{item.size <= 1024 * 1024 ? <Link href={`${page}?file=${encodeURIComponent(item.path)}`}>打开</Link> : "超过预览上限"}</td></tr>)}
    </tbody></table></div><p className="note">仅展示已接受结果的文件。预览前核对内容摘要，不直接提供存储凭证或对象地址。</p></section>
    {file && <section className="panel"><h2 className="identifier">{file}</h2>{problem ? <p role="alert">{problem}</p> : <pre className="artifact-text">{content}</pre>}<p className="note">按原始 UTF-8 文本显示，最多 1 MiB。usage / reasoning 缺失时保持未知，不生成补值。</p></section>}
  </main>;
}
