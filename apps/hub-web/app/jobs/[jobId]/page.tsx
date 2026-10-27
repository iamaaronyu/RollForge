import Link from "next/link";
import { notFound } from "next/navigation";
import { experimental, hub, HubProblem, labels, message, resultText, validId, type JobView, type JobAggregation, type ExecutionList, type TrialList } from "../../../lib/hub";
import { CancelJobButton, CancelTrialButton } from "./cancel-buttons";
import { RefreshWhileActive } from "../refresh-while-active";

export const dynamic = "force-dynamic";
export default async function Detail({ params, searchParams }: {
  params: Promise<{ jobId: string }>;
  searchParams: Promise<{ after?: string; trialAfter?: string; error?: string; notice?: string }>;
}) {
  return renderDetail(params, searchParams);
}
async function renderDetail(params: Promise<{ jobId: string }>, searchParams: Promise<{ after?: string; trialAfter?: string; error?: string; notice?: string }>) {
  const { jobId } = await params; const { after, trialAfter, error, notice } = await searchParams;
  if (!validId(jobId)) notFound();
  let job: JobView; let executions: ExecutionList; let trials: TrialList; let aggregation: JobAggregation;
  try {
    if ((after && !/^\d{1,10}$/.test(after)) || (trialAfter && !/^\d{1,3}$/.test(trialAfter))) throw new Error();
    [job, executions, trials, aggregation] = await Promise.all([
      hub<JobView>(`/api/v1/jobs/${jobId}`),
      hub<ExecutionList>(`/api/v1/jobs/${jobId}/executions?limit=20&after=${after ?? 0}`),
      hub<TrialList>(`/api/v1/jobs/${jobId}/trials?limit=20&after=${trialAfter ?? 0}`),
      hub<JobAggregation>(`/api/v1/jobs/${jobId}/aggregation`),
    ]);
  } catch (error) {
    if (error instanceof HubProblem && error.status === 404) notFound();
    return <main><Link href="/jobs">← 我的任务</Link><h1>暂时无法读取任务</h1><p role="alert">{message(error)}</p><Link href={`/jobs/${jobId}`}>重试</Link></main>;
  }
  return <main><header><Link href="/jobs">← 我的任务</Link><Link href={`/jobs/${jobId}`}>刷新</Link></header>
    <RefreshWhileActive active={job.terminal_trial_count < job.trial_count} />
    {error === "cancel" && <p role="alert">取消请求未完成，请刷新任务状态后重试。</p>}
    {notice === "cancelled" && <p role="status">取消请求已接受；Worker 会在下次续租检查时终止运行并清理 Sandbox。</p>}
    <h1>执行详情</h1><p className="identifier">{jobId}</p>
    <section className="panel"><h2>{job.terminal_trial_count} / {job.trial_count} 个样本已结束</h2>
      <p>首样本：{labels[job.trial.status]} · {resultText(job.trial.result)}</p>
      <dl><dt>Task Revision</dt><dd>{job.snapshot.task.id} · v{job.snapshot.task.revision}</dd><dt>执行预算</dt><dd>每个样本最多 {job.snapshot.max_executions} 次 · 超时 {job.snapshot.timeout_sec} 秒</dd></dl>
      <p className="note">样本序号 0 至 {job.trial_count - 1} 在创建时确定。0 分是有效评分；Retry 保留同一 Trial，仅创建新的 Execution。</p>
      {experimental() && job.terminal_trial_count < job.trial_count && <CancelJobButton jobId={jobId} />}
    </section>
    <section className="panel"><h2>多样本评分汇总</h2>
      <p>{aggregation.scored_trial_count} 个样本有有效评分；终态：完成 {aggregation.completed_trial_count}、失败 {aggregation.failed_trial_count}、取消 {aggregation.cancelled_trial_count}。</p>
      {Object.keys(aggregation.rewards).length ? <div className="table-wrap"><table><thead><tr><th>指标</th><th>平均值</th><th>最小值</th><th>最大值</th><th>覆盖数</th></tr></thead><tbody>
        {Object.entries(aggregation.rewards).map(([name, metric]) => <tr key={name}><td>{name}</td><td>{metric.mean}</td><td>{metric.minimum}</td><td>{metric.maximum}</td><td>{metric.sample_count} / {aggregation.scored_trial_count}</td></tr>)}
      </tbody></table></div> : <p>目前没有可汇总的评分指标。缺失值保持未知，不按零分处理。</p>}
      <p className="note">每个逻辑 Trial 只计入当前已接受结果；Retry 不会重复计数。各指标只对实际提供该指标的评分样本计算平均值，零分作为有效数值纳入。</p>
    </section>
    <section className="panel"><h2>逻辑 Trial 样本</h2>
      <div className="table-wrap"><table><thead><tr><th>样本序号</th><th>Trial</th><th>状态</th><th>执行序号</th><th>结果</th><th>操作</th></tr></thead><tbody>
        {trials.items.map(item => <tr key={item.trial_id}><td>{item.attempt_index}</td><td className="identifier">{item.trial_id}</td><td>{labels[item.status]}</td><td>{item.fencing_token || "未领取"}</td><td>{resultText(item.result)}</td><td>{experimental() && ["PENDING", "QUEUED", "RUNNING"].includes(item.status) ? <CancelTrialButton jobId={jobId} trialId={item.trial_id} /> : "—"}</td></tr>)}
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
