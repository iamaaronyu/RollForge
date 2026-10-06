import Link from "next/link";
import { randomUUID } from "node:crypto";
import { createJob } from "./actions";
import { experimental, hub, labels, message, resultText, validId, type JobList, type TaskList } from "../../lib/hub";

export const dynamic = "force-dynamic";
export default async function Jobs({ searchParams }: { searchParams: Promise<{ after?: string; error?: string }> }) {
  const params = await searchParams;
  let jobs: JobList | undefined; let tasks: TaskList | undefined; let problem = "";
  let writes = false;
  try {
    if (params.after && !validId(params.after)) throw new Error();
    [jobs, tasks, { control_plane_writes_enabled: writes }] = await Promise.all([
      hub<JobList>(`/api/v1/jobs?limit=20${params.after ? `&after=${params.after}` : ""}`),
      hub<TaskList>("/api/v1/tasks/approved"),
      hub<{ control_plane_writes_enabled: boolean }>("/api/v1/platform"),
    ]);
  } catch (error) { problem = message(error); }
  const enabled = experimental() && writes && Boolean(tasks?.items.length);
  return <main>
    <header><Link href="/">RollForge</Link><span>单任务实验</span></header>
    <h1>任务与执行</h1><p className="intro">查看自己的任务、评分和每次执行记录。</p>
    <section className="panel"><h2>创建任务</h2>
      <p className="note">只使用服务端已审核任务。正式执行入口尚未开放；实验运行需部署者配置，任务由 Worker 领取。</p>
      {problem && <p role="alert" className="alert">{problem}</p>}
      {params.error && <p role="alert" className="alert">创建未完成。请检查实验开关、已审核任务与服务状态；可先刷新列表确认结果。</p>}
      <form action={createJob}>
        <input type="hidden" name="jobId" value={randomUUID()} />
        <label htmlFor="task">已审核任务</label>
        <select id="task" name="taskId" disabled={!enabled} required>
          {!tasks?.items.length && <option value="">暂无可用任务</option>}
          {tasks?.items.map(task => <option key={task.id} value={task.id}>{task.label}</option>)}
        </select><button disabled={!enabled}>创建 Job</button>
      </form>
      {!enabled && <p className="note">当前仅可查看；创建需要服务端访问凭证、已审核目录、控制面写开关和 Web 实验开关。</p>}
    </section>
    <section className="panel"><div className="row"><h2>我的 Job</h2><Link href="/jobs">刷新</Link></div>
      {jobs?.items.length ? <div className="table-wrap"><table><thead><tr><th>Job</th><th>状态</th><th>结果</th><th>执行序号</th></tr></thead>
        <tbody>{jobs.items.map(job => <tr key={job.job_id}><td><Link href={`/jobs/${job.job_id}`}>{job.job_id}</Link></td><td>{labels[job.trial.status]}</td><td>{resultText(job.trial.result)}</td><td>{job.trial.fencing_token || "未领取"}</td></tr>)}</tbody></table></div> : <p>暂无任务记录。</p>}
      <nav>{jobs?.next_cursor && <Link href={`/jobs?after=${jobs.next_cursor}`}>下一页 →</Link>}</nav>
      <p className="note">按 Job ID 排序，每页最多 20 条。刷新以获取最新状态。</p>
    </section>
  </main>;
}
