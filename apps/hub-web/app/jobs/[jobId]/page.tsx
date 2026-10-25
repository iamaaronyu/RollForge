import Link from "next/link";
import { notFound } from "next/navigation";
import { hub, HubProblem, labels, message, resultText, validId, type JobView, type ExecutionList, type TrialList } from "../../../lib/hub";

export const dynamic = "force-dynamic";
export default async function Detail({ params, searchParams }: {
  params: Promise<{ jobId: string }>;
  searchParams: Promise<{ after?: string; trialAfter?: string }>;
}) {
  return renderDetail(params, searchParams);
}
async function renderDetail(params: Promise<{ jobId: string }>, searchParams: Promise<{ after?: string; trialAfter?: string }>) {
  const { jobId } = await params; const { after, trialAfter } = await searchParams;
  if (!validId(jobId)) notFound();
  let job: JobView; let executions: ExecutionList; let trials: TrialList;
  try {
    if ((after && !/^\d{1,10}$/.test(after)) || (trialAfter && !/^\d{1,3}$/.test(trialAfter))) throw new Error();
    [job, executions, trials] = await Promise.all([
      hub<JobView>(`/api/v1/jobs/${jobId}`),
      hub<ExecutionList>(`/api/v1/jobs/${jobId}/executions?limit=20&after=${after ?? 0}`),
      hub<TrialList>(`/api/v1/jobs/${jobId}/trials?limit=20&after=${trialAfter ?? 0}`),
    ]);
  } catch (error) {
    if (error instanceof HubProblem && error.status === 404) notFound();
    return <main><Link href="/jobs">← 我的任务</Link><h1>暂时无法读取任务</h1><p role="alert">{message(error)}</p><Link href={`/jobs/${jobId}`}>重试</Link></main>;
  }
  return <main><header><Link href="/jobs">← 我的任务</Link><Link href={`/jobs/${jobId}`}>刷新</Link></header>
    <h1>执行详情</h1><p className="identifier">{jobId}</p>
    <section className="panel"><h2>{job.terminal_trial_count} / {job.trial_count} 个样本已结束</h2>
      <p>首样本：{labels[job.trial.status]} · {resultText(job.trial.result)}</p>
      <dl><dt>Task Revision</dt><dd>{job.snapshot.task.id} · v{job.snapshot.task.revision}</dd><dt>执行预算</dt><dd>每个样本最多 {job.snapshot.max_executions} 次 · 超时 {job.snapshot.timeout_sec} 秒</dd></dl>
      <p className="note">样本序号 0 至 {job.trial_count - 1} 在创建时确定。0 分是有效评分；Retry 保留同一 Trial，仅创建新的 Execution。</p>
    </section>
    <section className="panel"><h2>逻辑 Trial 样本</h2>
      <div className="table-wrap"><table><thead><tr><th>样本序号</th><th>Trial</th><th>状态</th><th>执行序号</th><th>结果</th></tr></thead><tbody>
        {trials.items.map(item => <tr key={item.trial_id}><td>{item.attempt_index}</td><td className="identifier">{item.trial_id}</td><td>{labels[item.status]}</td><td>{item.fencing_token || "未领取"}</td><td>{resultText(item.result)}</td></tr>)}
      </tbody></table></div>
      <nav>{trials.next_cursor !== null && trials.next_cursor !== undefined && <Link href={`/jobs/${jobId}?trialAfter=${trials.next_cursor}`}>下一页样本 →</Link>}</nav>
    </section>
    <section className="panel"><h2>Execution 历史</h2>
      {executions.items.length ? <div className="table-wrap"><table><thead><tr><th>样本序号</th><th>执行序号</th><th>Execution</th><th>状态</th><th>结果</th><th>租约期限</th><th>产物</th></tr></thead><tbody>
        {executions.items.map(item => <tr key={item.execution_id}><td>{item.attempt_index}</td><td>{item.fencing_token}</td><td className="identifier">{item.execution_id}</td><td>{labels[item.status]}</td><td>{resultText(item.result)}</td><td>{new Date(item.expires_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</td><td>{item.result ? <Link href={`/jobs/${jobId}/executions/${item.execution_id}`}>查看</Link> : "尚无提交"}</td></tr>)}
      </tbody></table></div> : <p>任务尚未被 Worker 领取。</p>}
      <nav>{executions.next_cursor !== null && executions.next_cursor !== undefined && <Link href={`/jobs/${jobId}?after=${executions.next_cursor}`}>下一页 →</Link>}</nav>
      <p className="note">执行历史按 Job 内领取序号排序，并标出对应样本和该样本的 Retry 序号；租约期限不是完成时间。</p>
    </section>
  </main>;
}
