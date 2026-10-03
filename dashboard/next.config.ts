import type { NextConfig } from "next";

// Headers on every response. The public pages are static files, so these
// cost nothing; they matter more once the signed-in live pages exist.
const securityHeaders = [
  // Never sniff a file into a different type than the one served.
  { key: "X-Content-Type-Options", value: "nosniff" },
  // No other site may frame these pages (clickjacking on the approve page).
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
