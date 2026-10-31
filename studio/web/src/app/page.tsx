"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loading } from "@/components/ui";
import { useStudio } from "@/lib/studio";

export default function Home() {
  const { mode } = useStudio();
  const router = useRouter();
  useEffect(() => { if (mode) router.replace(mode === "pbi" ? "/pbi/ask" : "/inbox"); }, [mode, router]);
  return <div className="p-6"><Loading /></div>;
}
