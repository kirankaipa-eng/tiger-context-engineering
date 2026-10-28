import { IBM_Plex_Sans_Condensed, Open_Sans, Poppins } from "next/font/google";

const cond = IBM_Plex_Sans_Condensed({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-plex-cond" });
const poppins = Poppins({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-poppins" });
const openSans = Open_Sans({ subsets: ["latin"], weight: ["400", "600", "700"], variable: "--font-open-sans" });

export const metadata = { title: "Power BI analysis desk" };

export default function PbiLayout({ children }: { children: React.ReactNode }) {
  return <div className={`${cond.variable} ${poppins.variable} ${openSans.variable} h-full`}>{children}</div>;
}
