"use client";

import { Suspense } from "react";
import { Dashboards } from "@/components/dashboards";

export default function DashboardsPage() {
  // the open dashboard is in the address (?d=), read on the client
  return <Suspense><Dashboards /></Suspense>;
}
