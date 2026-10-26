"use client";

import { useMemo, useState } from "react";
import type { TrajectoryView } from "../../../../../lib/hub";

const sourceLabels = { system: "系统", user: "用户", agent: "Agent" } as const;

function searchableText(step: TrajectoryView["steps"][number]): string {
  return [
    step.message,
    step.reasoning ?? "",
    ...step.tools.flatMap((tool) => [tool.name, tool.call_id, tool.arguments_text]),
    ...step.observations.flatMap((item) => [item.call_id ?? "", item.text ?? ""]),
    ...step.media.flatMap((item) => [item.kind, item.media_type, item.artifact_path ?? ""]),
  ].join("\n").toLocaleLowerCase();
}

export function TrajectoryViewer({ trajectory }: { trajectory: TrajectoryView }) {
  const [query, setQuery] = useState("");
  const visibleSteps = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return trajectory.steps;
    return trajectory.steps.filter((step) => searchableText(step).includes(normalized));
  }, [query, trajectory.steps]);

  return <>
    <label htmlFor="trajectory-search">搜索轨迹内容</label>
    <input
      id="trajectory-search"
      type="search"
      value={query}
      onChange={(event) => setQuery(event.currentTarget.value)}
      placeholder="消息、推理、工具调用或返回内容"
    />
    <p role="status" aria-live="polite">显示 {visibleSteps.length} / {trajectory.steps.length} 步</p>
    {visibleSteps.length === 0 ? <p>没有匹配的轨迹步骤。</p> : visibleSteps.map((step) => <article className="panel" key={step.step_id}>
      <h3>#{step.step_id} · {sourceLabels[step.source]}</h3>
      <p>{step.timestamp ?? "时间未知"}{step.copied_context ? " · 复制上下文" : ""}</p>
      <pre className="artifact-text">{step.message}</pre>
      {step.tools.map((tool) => <details key={tool.call_id}><summary>工具：{tool.name} · {tool.call_id}</summary><pre className="artifact-text">{tool.arguments_text}</pre></details>)}
      {step.observations.map((item, i) => <details key={i}><summary>返回：{item.call_id ?? "无调用 ID"}</summary><pre className="artifact-text">{item.text ?? "内容未知"}</pre></details>)}
      {step.media.map((item, i) => <p key={`${item.kind}-${i}`}>{item.kind === "image" ? "图片" : "音频"}附件 · {item.media_type}{item.duration_sec === null ? "" : ` · ${item.duration_sec} 秒`}{item.artifact_path ? ` · 已包含：${item.artifact_path}` : " · 附件未包含在已接受产物中"}</p>)}
      {step.source === "agent" && <><p>输入 tokens：{step.usage?.prompt_tokens ?? "未知"} · 输出 tokens：{step.usage?.completion_tokens ?? "未知"} · 缓存 tokens：{step.usage?.cached_tokens ?? "未知"} · 成本 USD：{step.usage?.cost_usd ?? "未知"}</p><details><summary>已记录 reasoning</summary><pre className="artifact-text">{step.reasoning ?? "未知"}</pre></details></>}
    </article>)}
  </>;
}
