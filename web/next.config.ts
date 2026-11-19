import type { NextConfig } from "next";

const api = new URL(process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");
const extra = (process.env.NEXT_PUBLIC_EXTRA_CONNECT_SRC || "").split(/\s+/).filter(Boolean);
const dev = process.env.NODE_ENV !== "production";

// The terminal and live logs connect to a websocket service on the API host but often on another port.
const connect = [
  "'self'",
  api.origin,
  `http://${api.hostname}:*`,
  `https://${api.hostname}:*`,
  `ws://${api.hostname}:*`,
  `wss://${api.hostname}:*`,
  ...extra,
];

// Next.js injects inline bootstrap scripts, and dev mode (React refresh) also needs eval.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${connect.join(" ")}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  `form-action 'self' ${api.origin}`,
  "object-src 'none'",
].join("; ");

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
