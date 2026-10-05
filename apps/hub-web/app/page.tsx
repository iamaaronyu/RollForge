export const dynamic = "force-dynamic";

async function getStatus(): Promise<string> {
  try {
    const response = await fetch(
      `${process.env.ROLLFORGE_API_URL ?? "http://localhost:8000"}/health/live`,
      { cache: "no-store", signal: AbortSignal.timeout(3000) },
    );
    if (!response.ok) return "Unavailable";
    const health = await response.json();
    return health.status === "ok" ? "Online" : "Unavailable";
  } catch { return "Unavailable"; }
}

export default async function Home() {
  const status = await getStatus();
  return <main>
    <header><strong>RollForge</strong><span>Development preview</span></header>
    <p className="eyebrow">AGENT ROLLOUT & EVALUATION</p>
    <h1>Build experiments.<br />Understand every run.</h1>
    <p className="intro">A control plane for reproducible agent experiments,
      execution history, trajectories and rewards.</p>
    <section aria-label="Platform status">
      <div><span>Hub API</span><strong>{status}</strong></div>
      <div><span>Execution</span><strong>Not enabled</strong></div>
      <div><span>Current milestone</span><strong>Runtime compatibility</strong></div>
    </section>
    <p className="note">The project scaffold is ready. Real job execution will be enabled
      after Harbor, self-hosted E2B and the inference endpoint pass integration validation.</p>
    <a href="https://github.com/iamaaronyu/RollForge">View project on GitHub →</a>
  </main>;
}
