"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  Upload,
  Search,
  MessageSquare,
  FileText,
  FolderOpen,
  Network,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

const baseNavItems = [
  { href: "/", label: "Upload", icon: Upload },
  { href: "/search", label: "Search", icon: Search },
  { href: "/ask", label: "Ask AI", icon: MessageSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/collections", label: "Collections", icon: FolderOpen },
  { href: "/explore", label: "Explore", icon: Network },
];

export default function Header() {
  const pathname = usePathname();
  const [turboAvailable, setTurboAvailable] = useState(false);
  const [turboActive, setTurboActive] = useState(false);
  const [turboReady, setTurboReady] = useState(false);

  // Check turbo mode availability on mount
  useEffect(() => {
    const checkTurboStatus = async () => {
      try {
        const status = await api.getTurboStatus();
        setTurboAvailable(status.available);
        setTurboActive(status.active);
        setTurboReady(status.ready ?? false);
      } catch {
        // Turbo mode not available
        setTurboAvailable(false);
      }
    };

    checkTurboStatus();

    // Poll for status updates every 30 seconds (or more frequently if warming up)
    const pollInterval = turboActive && !turboReady ? 5000 : 30000;
    const interval = setInterval(checkTurboStatus, pollInterval);
    return () => clearInterval(interval);
  }, [turboActive, turboReady]);

  const isActive = (href: string) => {
    if (href === "/") {
      return pathname === "/";
    }
    return pathname.startsWith(href);
  };

  // Build nav items dynamically based on turbo mode availability
  const navItems = turboAvailable
    ? [...baseNavItems, { href: "/turbo", label: "Turbo", icon: Zap }]
    : baseNavItems;

  return (
    <header className="border-b border-border backdrop-blur-xl bg-background/80 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          <Link href="/" className="flex items-center gap-3">
            <Image
              src="/logo.svg"
              alt="MOCA Logo"
              width={40}
              height={40}
              className="h-10 w-auto"
              priority
            />
          </Link>

          <nav className="flex items-center gap-1 glass rounded-full p-1">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2 px-4 py-2 rounded-full transition-all duration-300",
                  isActive(item.href)
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  // Special styling for Turbo when ready (green) or warming up (yellow)
                  item.href === "/turbo" && turboReady && !isActive(item.href)
                    ? "text-green-400 hover:text-green-300"
                    : item.href === "/turbo" && turboActive && !isActive(item.href)
                    ? "text-yellow-400 hover:text-yellow-300"
                    : ""
                )}
              >
                <item.icon className={cn(
                  "w-4 h-4",
                  // Green for ready, yellow pulsing for warming up
                  item.href === "/turbo" && turboReady && "text-green-400",
                  item.href === "/turbo" && turboActive && !turboReady && "animate-pulse text-yellow-400"
                )} />
                <span className="text-sm font-medium hidden sm:inline">
                  {item.label}
                </span>
                {/* Show indicator dot: green when ready, yellow pulsing when warming up */}
                {item.href === "/turbo" && turboReady && (
                  <span className="w-2 h-2 bg-green-400 rounded-full" />
                )}
                {item.href === "/turbo" && turboActive && !turboReady && (
                  <span className="w-2 h-2 bg-yellow-400 rounded-full animate-pulse" />
                )}
              </Link>
            ))}
          </nav>
        </div>
      </div>
    </header>
  );
}
