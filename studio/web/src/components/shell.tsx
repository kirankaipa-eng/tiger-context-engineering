"use client";

/* The Power BI desk's frame. The Context Studio (ontology, metrics, mappings and their reviews) is its own app,
   in ../context, so a change there never moves anything here. */

import { PbiShell } from "./pbi-shell";

export function Shell({ children }: { children: React.ReactNode }) {
  return <PbiShell>{children}</PbiShell>;
}
