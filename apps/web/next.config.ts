import type { NextConfig } from "next";

const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // The page's title goes in the head of the first answer for every reader, not later in
  // the body: a screen reader names a page by it as soon as it arrives. Nothing here is
  // slow to work out, so nothing is gained by streaming it.
  htmlLimitedBots: /.*/,
  headers: async () => [{ source: "/:path*", headers: SECURITY_HEADERS }],
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
