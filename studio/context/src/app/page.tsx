"use client";

/* The Studio opens on the inbox: what is waiting for the person signed in. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loading } from "@/components/ui";

export default function Home() {
  const router = useRouter();
  useEffect(() => { router.replace("/inbox"); }, [router]);
  return <div className="p-6"><Loading /></div>;
}
