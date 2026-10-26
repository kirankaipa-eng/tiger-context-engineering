import type { NextConfig } from "next";

// NEXT_DIST_DIR lets a second dev server (e.g. the business demo on another port) run from this folder.
const nextConfig: NextConfig = { devIndicators: false, distDir: process.env.NEXT_DIST_DIR || ".next" };

export default nextConfig;
