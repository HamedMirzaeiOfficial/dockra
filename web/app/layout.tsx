import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { NotifyHost } from "@/components/notify";
import { THEME_SCRIPT } from "@/lib/theme-script";
import "./globals.css";

// Self-hosted (OFL) so builds never reach out to a font CDN — the panel must build on isolated networks too.
const inter = localFont({
  src: "../node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
  weight: "100 900",
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: { default: "Dockra", template: "%s · Dockra" },
  description: "Dockra — a self-hosted deployment platform. From code to cloud.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#090a0e" },
    { media: "(prefers-color-scheme: light)", color: "#f6f7fa" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the saved theme before first paint so there is no flash of the wrong palette. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className={`${inter.variable} min-h-screen bg-bg font-sans text-fg antialiased`} suppressHydrationWarning>
        {children}
        <NotifyHost />
      </body>
    </html>
  );
}
