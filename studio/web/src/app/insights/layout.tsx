import { IBM_Plex_Sans_Condensed } from "next/font/google";

const cond = IBM_Plex_Sans_Condensed({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-cond" });

export const metadata = { title: "Medical economics desk" };

export default function DeskLayout({ children }: { children: React.ReactNode }) {
  return <div className={`${cond.variable} desk h-screen`}>{children}</div>;
}
