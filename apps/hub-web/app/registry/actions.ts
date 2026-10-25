"use server";

import { redirect } from "next/navigation";
import { experimental, hub, validId, type JobView } from "../../lib/hub";

const digestPattern = /^sha256:[0-9a-f]{64}$/;

export async function createRevision(form: FormData) {
  const assetId = String(form.get("assetId") ?? "");
  const revision = Number(form.get("revision") ?? "");
  const kind = String(form.get("kind") ?? "");
  if (!validId(assetId) || !Number.isInteger(revision) || revision < 1 || revision > 1_000_000) {
    redirect("/registry?error=invalid");
  }
  let spec: Record<string, string>;
  if (kind === "TASK") {
    const archiveKey = String(form.get("archiveKey") ?? "");
    const archiveDigest = String(form.get("archiveDigest") ?? "");
    if (!archiveKey || archiveKey.length > 512 || !digestPattern.test(archiveDigest)) {
      redirect("/registry?error=invalid");
    }
    spec = { kind, archive_key: archiveKey, archive_digest: archiveDigest };
  } else if (kind === "AGENT") {
    spec = { kind, agent_name: "claude-code", agent_version: "2.1.81" };
  } else if (kind === "MODEL") {
    const modelBaseUrl = String(form.get("modelBaseUrl") ?? "");
    if (!modelBaseUrl || modelBaseUrl.length > 512) redirect("/registry?error=invalid");
    spec = {
      kind,
      model_name: "deepseek-flash",
      protocol: "anthropic-messages",
      model_base_url: modelBaseUrl,
      credential_mode: "gateway-session",
    };
  } else {
    redirect("/registry?error=invalid");
  }
  try {
    await hub("/api/v1/registry/revisions", {
      asset_id: assetId,
      revision,
      spec,
    });
  } catch {
    redirect("/registry?error=revision");
  }
  redirect("/registry?created=revision");
}

function readRevisionReference(value: FormDataEntryValue | null) {
  const [id, revisionText, digest, extra] = String(value ?? "").split("|");
  const revision = Number(revisionText);
  if (
    extra !== undefined ||
    !validId(id) ||
    !Number.isInteger(revision) ||
    revision < 1 ||
    revision > 1_000_000 ||
    !digestPattern.test(digest)
  ) {
    return undefined;
  }
  return { id, revision, digest };
}

export async function createJobFromRevisions(form: FormData) {
  const jobId = String(form.get("jobId") ?? "");
  const trialCount = Number(form.get("trialCount") ?? 1);
  const task = readRevisionReference(form.get("task"));
  const agent = readRevisionReference(form.get("agent"));
  const model = readRevisionReference(form.get("model"));
  if (
    !experimental() ||
    !validId(jobId) ||
    !Number.isInteger(trialCount) ||
    trialCount < 1 ||
    trialCount > 1000 ||
    !task ||
    !agent ||
    !model
  ) {
    redirect("/registry?error=disabled");
  }
  try {
    await hub<JobView>("/api/v1/jobs/from-revisions", {
      job_id: jobId,
      revisions: { task, agent, model },
      trial_count: trialCount,
    });
  } catch {
    redirect("/registry?error=job");
  }
  redirect(`/jobs/${jobId}`);
}
