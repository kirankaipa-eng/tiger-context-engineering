"use client";

/* The desk opens on Ask. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loading } from "@/components/ui";

export default function Home() {
  const router = useRouter();
  useEffect(() => { router.replace("/pbi/ask"); }, [router]);
  return <div className="p-6"><Loading /></div>;
}
