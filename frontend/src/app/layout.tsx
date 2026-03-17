import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
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

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`dark ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        <style
          dangerouslySetInnerHTML={{
            __html: `:root,.dark{--accent:${process.env.NEXT_PUBLIC_ACCENT_COLOR || "oklch(0.6619 0.1787 268.72)"}}`,
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
