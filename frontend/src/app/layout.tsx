import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";
import { LayoutWrapper, AuthProvider } from "@/components/layout";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "MOCA Knowledge Base",
  description: "AI-powered knowledge base with Neo4j + Haystack",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Force dynamic rendering so process.env is read at runtime, not build time
  await headers();

  // Use ACCENT_COLOR (non-NEXT_PUBLIC_) so it's read from process.env at runtime
  // rather than being inlined at build time by Next.js webpack DefinePlugin.
  // Values with # (hex colors) don't survive Dokploy's env var interpolation,
  // so accent colors should use oklch/rgb/hsl format instead of hex.
  const accentColor = process.env.ACCENT_COLOR || "oklch(0.79 0.18 70.67)";

  return (
    <html
      lang="en"
      className={`dark ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        <style
          dangerouslySetInnerHTML={{
            __html: `:root,.dark{--accent:${accentColor}}`,
          }}
        />
      </head>
      <body className="font-sans antialiased">
        <div className="min-h-screen bg-background flex flex-col">
          <AuthProvider>
            <LayoutWrapper>{children}</LayoutWrapper>
          </AuthProvider>
        </div>
      </body>
    </html>
  );
}
