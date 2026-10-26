"use client";

import { cancelJob, cancelTrial } from "../actions";

export function CancelJobButton({ jobId }: { jobId: string }) {
  return <form action={cancelJob} onSubmit={(event) => {
    if (!window.confirm("取消此 Job 的所有未结束 Trial？Worker 会在下次续租检查时终止运行并清理 Sandbox。")) event.preventDefault();
  }}>
    <input type="hidden" name="jobId" value={jobId} />
    <button type="submit">取消未完成样本</button>
  </form>;
}

export function CancelTrialButton({ jobId, trialId }: { jobId: string; trialId: string }) {
  return <form action={cancelTrial} onSubmit={(event) => {
    if (!window.confirm("取消这个 Trial？Worker 会在下次续租检查时终止运行并清理 Sandbox。")) event.preventDefault();
  }}>
    <input type="hidden" name="jobId" value={jobId} />
    <input type="hidden" name="trialId" value={trialId} />
    <button type="submit">取消</button>
  </form>;
}
