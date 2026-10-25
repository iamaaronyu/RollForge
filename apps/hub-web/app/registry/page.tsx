import Link from "next/link";
import { randomUUID } from "node:crypto";
import { createJobFromRevisions, createRevision } from "./actions";
import {
  experimental,
  hub,
  message,
  validId,
  type AssetRevision,
  type RegistryAssetList,
  type RevisionList,
} from "../../lib/hub";

export const dynamic = "force-dynamic";

const kinds = { TASK: "任务", AGENT: "Agent", MODEL: "模型" };
type Kind = keyof typeof kinds;

function reference(revision: AssetRevision) {
  return `${revision.asset_id}|${revision.revision}|${revision.digest}`;
}

function revisionDescription(revision: AssetRevision) {
  if (revision.spec.kind === "TASK") return `归档：${revision.spec.archive_key}`;
  if (revision.spec.kind === "AGENT") return `${revision.spec.agent_name} ${revision.spec.agent_version}`;
  return `${revision.spec.model_name} · ${revision.spec.protocol} · ${revision.spec.model_base_url}`;
}

export default async function RegistryPage({
  searchParams,
}: {
  searchParams: Promise<{ after?: string; error?: string; created?: string }>;
}) {
  const params = await searchParams;
  let assets: RegistryAssetList | undefined;
  let revisions: AssetRevision[] = [];
  let writes = false;
  let problem = "";
  try {
    if (params.after && !validId(params.after)) throw new Error();
    const [assetPage, platform] = await Promise.all([
      hub<RegistryAssetList>(
        `/api/v1/registry/assets?limit=50${params.after ? `&after=${params.after}` : ""}`,
      ),
      hub<{ control_plane_writes_enabled: boolean }>("/api/v1/platform"),
    ]);
    assets = assetPage;
    writes = platform.control_plane_writes_enabled;
    const revisionPages = await Promise.all(
      assets.items.map((asset) =>
        hub<RevisionList>(
          `/api/v1/registry/assets/${asset.asset_id}/revisions?limit=100&after=${Math.max(0, asset.latest_revision - 100)}`,
        ),
      ),
    );
    revisions = revisionPages.flatMap((page) => page.items);
  } catch (error) {
    problem = message(error);
  }
  const latest = (kind: Kind) => revisions.filter((item) => item.spec.kind === kind);
  const tasks = latest("TASK");
  const agents = latest("AGENT");
  const models = latest("MODEL");
  const canCreateJob = experimental() && writes && tasks.length > 0 && agents.length > 0 && models.length > 0;
  const canRegister = writes;

  return (
    <main>
      <header>
        <Link href="/">RollForge</Link>
        <span>不可变资产 Registry</span>
      </header>
      <nav className="page-nav"><Link href="/jobs">任务与执行</Link><Link href="/registry">资产版本</Link></nav>
      <h1>资产版本</h1>
      <p className="intro">登记固定的任务、Agent 和模型配置，再用指定版本创建评测 Job。登记不代表获准执行。</p>
      {problem && <p role="alert" className="alert">{problem}</p>}
      {params.created && <p role="status" className="success">版本已登记。</p>}
      {params.error === "invalid" && <p role="alert" className="alert">输入格式不正确，请检查资产 ID、版本号和摘要。</p>}
      {params.error === "revision" && <p role="alert" className="alert">版本未登记。请确认该资产属于你、版本连续且内容没有冲突。</p>}
      {params.error === "job" && <p role="alert" className="alert">Job 未创建。所选版本必须完整匹配部署者审核的运行配置；刷新资产后可重试。</p>}
      {params.error === "disabled" && <p role="alert" className="alert">当前未启用实验执行，或选择的数据不完整。</p>}

      <section className="panel">
        <h2>登记新版本</h2>
        <p className="note">同一资产使用相同 ID 并递增版本号；提交后内容不可修改。任务归档需先由运维人员放入已审核存储路径。表单不接收任何 API Key。</p>
        <form action={createRevision} className="registry-form">
          <label htmlFor="kind">资产类型</label>
          <select id="kind" name="kind" defaultValue="TASK" disabled={!canRegister}>
            {Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <label htmlFor="assetId">资产 ID</label>
          <input id="assetId" name="assetId" defaultValue={randomUUID()} required disabled={!canRegister} />
          <label htmlFor="revision">版本号</label>
          <input id="revision" name="revision" type="number" min="1" max="1000000" defaultValue="1" required disabled={!canRegister} />
          <label htmlFor="archiveKey">任务归档 Key</label>
          <input id="archiveKey" name="archiveKey" maxLength={512} placeholder="tasks/approved-task.tar.gz" disabled={!canRegister} />
          <label htmlFor="archiveDigest">任务归档 SHA-256</label>
          <input id="archiveDigest" name="archiveDigest" placeholder="sha256:…" disabled={!canRegister} />
          <label htmlFor="modelBaseUrl">模型网关地址</label>
          <input id="modelBaseUrl" name="modelBaseUrl" type="url" placeholder="https://gateway.example" disabled={!canRegister} />
          <button disabled={!canRegister}>登记不可变版本</button>
        </form>
        {!canRegister && <p className="note">登记需要服务端 USER 凭证和控制面写开关。</p>}
      </section>

      <section className="panel">
        <h2>从固定版本创建 Job</h2>
        <p className="note">执行快照会固定此处版本摘要。Hub 还会核对部署者审核的完整运行组合；页面选择不会绕过审核。</p>
        <form action={createJobFromRevisions} className="registry-form">
          <input type="hidden" name="jobId" value={randomUUID()} />
          <label htmlFor="task">任务版本</label>
          <select id="task" name="task" required disabled={!canCreateJob}>
            {tasks.map((item) => <option key={reference(item)} value={reference(item)}>{item.asset_id} · v{item.revision} · {item.spec.kind === "TASK" ? item.spec.archive_key : ""}</option>)}
          </select>
          <label htmlFor="agent">Agent 版本</label>
          <select id="agent" name="agent" required disabled={!canCreateJob}>
            {agents.map((item) => <option key={reference(item)} value={reference(item)}>{item.asset_id} · v{item.revision} · {revisionDescription(item)}</option>)}
          </select>
          <label htmlFor="model">模型版本</label>
          <select id="model" name="model" required disabled={!canCreateJob}>
            {models.map((item) => <option key={reference(item)} value={reference(item)}>{item.asset_id} · v{item.revision} · {revisionDescription(item)}</option>)}
          </select>
          <label htmlFor="trialCount">独立样本数</label>
          <input id="trialCount" name="trialCount" type="number" min="1" max="1000" defaultValue="1" required disabled={!canCreateJob} />
          <button disabled={!canCreateJob}>创建审核范围内的 Job</button>
        </form>
        {!canCreateJob && <p className="note">创建需要每类至少一个版本、服务端写入开关和 Web 实验开关。</p>}
      </section>

      <section className="panel">
        <div className="row"><h2>我的资产</h2><Link href="/registry">刷新</Link></div>
        {revisions.length ? <div className="table-wrap"><table><thead><tr><th>类型</th><th>资产 ID</th><th>最新版本</th><th>摘要</th><th>配置</th></tr></thead>
          <tbody>{revisions.map((item) => <tr key={reference(item)}><td>{kinds[item.spec.kind]}</td><td className="identifier">{item.asset_id}</td><td>{item.revision}</td><td className="identifier">{item.digest}</td><td>{revisionDescription(item)}</td></tr>)}</tbody></table></div> : <p>此页没有可见的资产；创建资产后会显示最新版本。</p>}
        {assets?.next_cursor && <nav><Link href={`/registry?after=${assets.next_cursor}`}>下一页资产 →</Link></nav>}
        <p className="note">资产按 ID 分页，每页最多 50 个；每项最多展示最近 100 个不可变版本。Job 创建可选择列表中的任一版本。</p>
      </section>
    </main>
  );
}
