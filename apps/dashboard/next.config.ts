import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Separate build output for the Playwright run (it must not touch the developer's `next dev`).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    instantInsights: {
      // The console is gated by a client-side session check (token verified against the API's
      // /auth/me), so console pages render only after hydration and can't be validated as part of
      // a static shell. Validate only segments that explicitly export `instant`.
      validationLevel: "manual-warning",
    },
  },
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
