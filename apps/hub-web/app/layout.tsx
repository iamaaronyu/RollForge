import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RollForge", description: "Agent 执行与评测平台",
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
