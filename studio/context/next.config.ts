import type { NextConfig } from "next";

// NEXT_DIST_DIR lets a second dev server (e.g. the business demo on another port) run from this folder.
// The Context Studio: the ontology, metrics and mappings, reviewed by their owners. The Power BI desk is ../web.
const nextConfig: NextConfig = { devIndicators: false, distDir: process.env.NEXT_DIST_DIR || ".next" };

export default nextConfig;
