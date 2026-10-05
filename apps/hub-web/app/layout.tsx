import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RollForge", description: "Agent rollout and evaluation platform",
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
