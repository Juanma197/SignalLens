import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The app's pages lived under /prototype until 2026-10-10. Old bookmarks and the
  // links in earlier Telegram alerts keep working; query strings carry over.
  redirects() {
    return [
      {source: "/prototype", destination: "/shortlist", permanent: true},
      {source: "/prototype/monthly", destination: "/", permanent: true},
      {source: "/prototype/:path*", destination: "/:path*", permanent: true},
    ];
  },
};

export default nextConfig;
