"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function RefreshWhileActive({ active }: { active: boolean }) {
  const router = useRouter();

  useEffect(() => {
    if (!active) return;

    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    const timer = window.setInterval(refreshIfVisible, 10_000);
    document.addEventListener("visibilitychange", refreshIfVisible);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [active, router]);

  if (!active) return null;
  return <p className="note" role="status">任务运行期间页面可见时每 10 秒自动更新。</p>;
}
