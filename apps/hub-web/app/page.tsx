import Link from "next/link";
export default function Home() {
  return <main><header><strong>RollForge</strong><span>受控实验平台</span></header>
    <p className="eyebrow">AGENT ROLLOUT & EVALUATION</p><h1>运行实验，<br />理解每次执行。</h1>
    <p className="intro">任务状态、执行历史和评分记录，由统一控制面管理。</p>
    <section className="panel"><h2>单任务执行闭环已验证</h2><p>真实执行、零分、结果上传、提交恢复及 Retry 隔离已有验收证据。正式执行入口保持关闭。</p><Link className="primary" href="/jobs">查看我的任务 →</Link></section>
    <p className="note">当前为本机单用户实验界面。登录、多租户隔离和生产网关仍待验收。</p>
    <a href="https://github.com/iamaaronyu/RollForge">GitHub 项目 ↗</a>
  </main>;
}
