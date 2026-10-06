import "server-only";
import type { components } from "./api-types";

export type JobList = components["schemas"]["JobList"];
export type JobView = components["schemas"]["JobView"];
export type ExecutionList = components["schemas"]["ExecutionList"];
export type TrajectoryView = components["schemas"]["TrajectoryView"];
export type ArtifactIndex = components["schemas"]["ArtifactIndex"];
export type TaskList = components["schemas"]["ApprovedTaskList"];
export class HubProblem extends Error {
  constructor(public readonly status: number) {
    super(status === 401 || status === 403 ? "服务端访问配置未就绪。" :
      status === 404 ? "记录不存在或无权访问。" : status === 409 ? "请求冲突，请刷新后查看。" :
      status === 503 ? "服务暂不可用或写入尚未启用。" : "请求未完成，请稍后重试。");
  }
}
export const validId = (value: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const experimental = () => process.env.ROLLFORGE_WEB_ALLOW_EXPERIMENTAL_RUNS === "true";
async function request(path: string, body?: unknown): Promise<Response> {
  const token = process.env.ROLLFORGE_WEB_USER_TOKEN;
  if (!token) throw new HubProblem(401);
  let response: Response;
  try {
    const base = new URL(process.env.ROLLFORGE_API_URL ?? "http://127.0.0.1:8000");
    if (base.username || base.password || base.search || base.hash || base.pathname !== "/" ||
      !(base.protocol === "https:" || (base.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)))) throw new Error();
    response = await fetch(new URL(path, base), {
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
  } catch { throw new HubProblem(503); }
  if (!response.ok) throw new HubProblem(response.status);
  return response;
}
export async function hub<T>(path: string, body?: unknown): Promise<T> {
  const response = await request(path, body);
  try { return await response.json() as T; } catch { throw new HubProblem(502); }
}
export async function hubText(path: string): Promise<string> {
  const response = await request(path);
  try { return await response.text(); } catch { throw new HubProblem(502); }
}
export function message(error: unknown): string {
  return error instanceof HubProblem ? error.message : "服务暂不可用，请稍后重试。";
}
export const labels: Record<components["schemas"]["TrialStatus"] | components["schemas"]["ExecutionStatus"], string> = {
  PENDING: "待准备", QUEUED: "排队中", RUNNING: "运行中", COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已取消", EXPIRED: "租约已过期",
};
export function resultText(result: components["schemas"]["ResultCommit"] | null | undefined) {
  if (!result) return "尚无提交结果";
  if (result.outcome === "SCORED") return Object.entries(result.rewards ?? {}).map(([name, value]) => `${name}：${value}`).join(" · ");
  const reasons = { AGENT_ERROR: "Agent 执行异常", VERIFIER_ERROR: "验证异常", INFRA_ERROR: "基础设施异常", UNVERIFIED: "尚未验证" };
  return result.failure_reason ? reasons[result.failure_reason] : "尚未评分";
}
