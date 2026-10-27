/** @type {import('next').NextConfig} */
const nextConfig = {
  agentRules: false,
  output: "standalone",
  distDir: process.env.PDFAGENT_NEXT_DIST_DIR || ".next",
};

export default nextConfig;
