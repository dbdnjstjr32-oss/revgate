import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // @revgate/contracts ships TypeScript sources (packages/contracts/ts) — SSOT types + SCHEMA_VERSION.
  transpilePackages: ["@revgate/contracts"],
};

export default nextConfig;
