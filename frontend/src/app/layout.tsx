import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Header, Footer, StatsBar } from "@/components/layout";

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
    <html lang="en" className={`dark ${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans antialiased">
        <div className="min-h-screen bg-background flex flex-col">
          <Header />
          <StatsBar />
          <main className="max-w-7xl mx-auto px-6 pb-12 w-full flex-1">
            {children}
          </main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
