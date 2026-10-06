import Link from "next/link";
import { notFound } from "next/navigation";
import { hub, HubProblem, labels, message, resultText, validId, type JobView, type ExecutionList } from "../../../lib/hub";

export const dynamic = "force-dynamic";
export default async function Detail({ params, searchParams }: { params: Promise<{ jobId: string }>; searchParams: Promise<{ after?: string }> }) {
  const { jobId } = await params; const { after } = await searchParams;
  if (!validId(jobId)) notFound();
  let job: JobView; let executions: ExecutionList;
  try {
    if (after && !/^\d{1,10}$/.test(after)) throw new Error();
    [job, executions] = await Promise.all([
      hub<JobView>(`/api/v1/jobs/${jobId}`),
      hub<ExecutionList>(`/api/v1/jobs/${jobId}/executions?limit=20&after=${after ?? 0}`),
    ]);
  } catch (error) {
    if (error instanceof HubProblem && error.status === 404) notFound();
    return <main><Link href="/jobs">← 我的任务</Link><h1>暂时无法读取任务</h1><p role="alert">{message(error)}</p><Link href={`/jobs/${jobId}`}>重试</Link></main>;
  }
  return <main><header><Link href="/jobs">← 我的任务</Link><Link href={`/jobs/${jobId}`}>刷新</Link></header>
    <h1>执行详情</h1><p className="identifier">{jobId}</p>
    <section className="panel"><h2>{labels[job.trial.status]}</h2><p className="score">{resultText(job.trial.result)}</p>
      <dl><dt>逻辑 Trial</dt><dd>{job.trial.trial_id}</dd><dt>Task Revision</dt><dd>{job.snapshot.task.id} · v{job.snapshot.task.revision}</dd><dt>执行预算</dt><dd>最多 {job.snapshot.max_executions} 次 · 超时 {job.snapshot.timeout_sec} 秒</dd></dl>
      <p className="note">0 分是有效评分。Retry 保留逻辑 Trial，创建新的 Execution。</p>
    </section>
    <section className="panel"><h2>Execution 历史</h2>
      {executions.items.length ? <div className="table-wrap"><table><thead><tr><th>序号</th><th>Execution</th><th>状态</th><th>结果</th><th>租约期限</th><th>产物</th></tr></thead><tbody>
        {executions.items.map(item => <tr key={item.execution_id}><td>{item.fencing_token}</td><td className="identifier">{item.execution_id}</td><td>{labels[item.status]}</td><td>{resultText(item.result)}</td><td>{new Date(item.expires_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</td><td>{item.result ? <Link href={`/jobs/${jobId}/executions/${item.execution_id}`}>查看</Link> : "尚无提交"}</td></tr>)}
      </tbody></table></div> : <p>任务尚未被 Worker 领取。</p>}
      <nav>{executions.next_cursor && <Link href={`/jobs/${jobId}?after=${executions.next_cursor}`}>下一页 →</Link>}</nav>
      <p className="note">租约期限不是完成时间。当前按执行序号分页；已提交执行可查看产物索引和文本预览。</p>
    </section>
  </main>;
}
