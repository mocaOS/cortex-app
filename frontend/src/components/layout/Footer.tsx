"use client";

import Image from "next/image";

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border mt-8">
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex items-center justify-center gap-3">
          <Image
            src="/brand/cortex_logo_white.svg"
            alt="Cortex"
            width={28}
            height={28}
            className="h-7 w-7 opacity-60 hover:opacity-100 transition-opacity duration-300"
            unoptimized
          />
          <p className="text-xs text-muted-foreground">
            © {year} Cortex · Institutional Memory for the Agentic Era
          </p>
        </div>
      </div>
    </footer>
  );
}
