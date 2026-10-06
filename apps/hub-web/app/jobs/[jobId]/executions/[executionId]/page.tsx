import Link from "next/link";
import { notFound } from "next/navigation";
import { hub, hubText, HubProblem, message, resultText, validId, type ArtifactIndex, type TrajectoryView } from "../../../../../lib/hub";

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
  let trajectory: TrajectoryView | undefined; let trajectoryProblem = "";
  if (index.files.some(item => item.path === "agent/trajectory.json")) {
    try { trajectory = await hub<TrajectoryView>(base + "/trajectory"); }
    catch (error) { trajectoryProblem = error instanceof HubProblem && [413, 422].includes(error.status) ? "轨迹超出投影范围或格式不受支持，可查看原始文件。" : message(error); }
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
      <p className="note">只展示主轨迹，保留原始顺序；用量不累加，复制上下文单独标记。缺失字段显示未知。图片、音频、子轨迹和扩展字段请查阅原始文件。</p>
      {trajectoryProblem && <p role="alert">{trajectoryProblem}</p>}
      {!trajectory && !trajectoryProblem && <p>尚无可展示的主轨迹。</p>}
      {trajectory && <><p>来源 {trajectory.source_version} · 投影版本 {trajectory.projection_version}</p>
        {trajectory.steps.map(step => <article className="panel" key={step.step_id}>
          <h3>#{step.step_id} · {{ system: "系统", user: "用户", agent: "Agent" }[step.source]}</h3>
          <p>{step.timestamp ?? "时间未知"}{step.copied_context ? " · 复制上下文" : ""}</p>
          <pre className="artifact-text">{step.message}</pre>
          {step.tools.map(tool => <details key={tool.call_id}><summary>工具：{tool.name} · {tool.call_id}</summary><pre className="artifact-text">{tool.arguments_text}</pre></details>)}
          {step.observations.map((item, i) => <details key={i}><summary>返回：{item.call_id ?? "无调用 ID"}</summary><pre className="artifact-text">{item.text ?? "内容未知"}</pre></details>)}
          {step.source === "agent" && <><p>输入 tokens：{step.usage?.prompt_tokens ?? "未知"} · 输出 tokens：{step.usage?.completion_tokens ?? "未知"} · 缓存 tokens：{step.usage?.cached_tokens ?? "未知"} · 成本 USD：{step.usage?.cost_usd ?? "未知"}</p><details><summary>已记录 reasoning</summary><pre className="artifact-text">{step.reasoning ?? "未知"}</pre></details></>}
        </article>)}
      </>}
    </section>
    <section className="panel"><h2>文件索引</h2><div className="table-wrap"><table><thead><tr><th>文件</th><th>字节</th><th>预览</th></tr></thead><tbody>
      {index.files.map(item => <tr key={item.path}><td className="identifier">{item.path}</td><td>{item.size}</td><td>{item.size <= 1024 * 1024 ? <Link href={`${page}?file=${encodeURIComponent(item.path)}`}>打开</Link> : "超过预览上限"}</td></tr>)}
    </tbody></table></div><p className="note">仅展示已接受结果的文件。预览前核对内容摘要，不直接提供存储凭证或对象地址。</p></section>
    {file && <section className="panel"><h2 className="identifier">{file}</h2>{problem ? <p role="alert">{problem}</p> : <pre className="artifact-text">{content}</pre>}<p className="note">按原始 UTF-8 文本显示，最多 1 MiB。usage / reasoning 缺失时保持未知，不生成补值。</p></section>}
  </main>;
}
