"use client";

import Image from "next/image";

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border mt-8">
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
          <Image
            src="/brand/cortex_logo_white.svg"
            alt="Cortex"
            width={28}
            height={28}
            className="h-7 w-7 opacity-60 hover:opacity-100 transition-opacity duration-300"
            unoptimized
          />
          <p className="text-xs text-muted-foreground">
            © {year} Cortex · AI-powered knowledge base
          </p>
        </div>
      </div>
    </footer>
  );
}
