"use client";

/* The Power BI home is the Ask page. A link that still comes here (/pbi?q=...) opens Ask with its question. */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function PbiHome() {
  const router = useRouter();
  useEffect(() => { router.replace(`/pbi/ask${window.location.search}`); }, [router]);
  return null;
}
