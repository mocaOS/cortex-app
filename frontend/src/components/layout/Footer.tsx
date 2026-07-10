"use client";

import Image from "next/image";
import { Twitter } from "lucide-react";

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border mt-8">
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
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
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">Built by</span>
            <a
              href="https://museumofcryptoart.com/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Museum of Crypto Art"
            >
              <Image
                src="/brand/moca_logo_white.svg"
                alt="MOCA"
                width={132}
                height={37}
                className="h-5 w-auto opacity-60 hover:opacity-100 transition-opacity duration-300"
                unoptimized
              />
            </a>
            <a
              href="https://twitter.com/MuseumofCrypto/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="MOCA on Twitter"
              className="text-muted-foreground hover:text-foreground transition-colors duration-300"
            >
              <Twitter className="h-4 w-4" />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
