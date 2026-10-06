"use server";
import { redirect } from "next/navigation";
import { experimental, hub, validId, type JobView } from "../../lib/hub";

export async function createJob(form: FormData) {
  const jobId = String(form.get("jobId") ?? "");
  const taskId = String(form.get("taskId") ?? "");
  if (!experimental() || !validId(jobId) || !/^[a-zA-Z0-9_-]{1,64}$/.test(taskId)) redirect("/jobs?error=disabled");
  try {
    await hub<JobView>("/api/v1/jobs/from-approved-task", { job_id: jobId, task_id: taskId });
  } catch { redirect("/jobs?error=create"); }
  redirect(`/jobs/${jobId}`);
}
