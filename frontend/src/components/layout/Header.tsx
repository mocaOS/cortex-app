"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  Database,
  Compass,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  basePaths: string[];
}

const navItems: NavItem[] = [
  {
    label: "Manage",
    icon: Database,
    href: "/documents",
    basePaths: ["/", "/documents", "/collections", "/add", "/extract", "/deduplicate", "/entities", "/relationships", "/communities"],
  },
  {
    label: "Explore",
    icon: Compass,
    href: "/explore",
    basePaths: ["/explore"],
  },
];

export default function Header() {
  const pathname = usePathname();
  const [turboAvailable, setTurboAvailable] = useState(false);
  const [turboActive, setTurboActive] = useState(false);
  const [turboReady, setTurboReady] = useState(false);

  // Helper to extract file extension from URL
  const getLogoExtension = (url: string): string => {
    const urlPath = url.split("?")[0];
    const ext = urlPath.split(".").pop() || "svg";
    return ext;
  };

  // Check turbo mode availability on mount
  useEffect(() => {
    const checkTurboStatus = async () => {
      try {
        const status = await api.getTurboStatus();
        setTurboAvailable(status.available);
        setTurboActive(status.active);
        setTurboReady(status.ready ?? false);
      } catch {
        setTurboAvailable(false);
      }
    };

    checkTurboStatus();

    const pollInterval = turboActive && !turboReady ? 5000 : 30000;
    const interval = setInterval(checkTurboStatus, pollInterval);
    return () => clearInterval(interval);
  }, [turboActive, turboReady]);

  // Check if a nav item is active
  const isNavActive = (item: NavItem): boolean => {
    if (item.basePaths.includes("/")) {
      if (pathname === "/") return true;
      return item.basePaths.some(
        (path) => path !== "/" && pathname.startsWith(path)
      );
    }
    return item.basePaths.some((path) => pathname.startsWith(path));
  };

  const isSettingsActive = pathname.startsWith("/admin");

  return (
    <header className="border-b border-border backdrop-blur-xl bg-background/80 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          <Link href="/documents" className="flex items-center gap-3">
            <Image
              src={
                process.env.NEXT_PUBLIC_LOGO_URL
                  ? `/custom-logo.${getLogoExtension(process.env.NEXT_PUBLIC_LOGO_URL)}`
                  : "/logo.svg"
              }
              alt="Logo"
              width={45}
              height={45}
              className="h-10 w-auto"
              priority
            />
          </Link>

          <div className="flex items-center gap-3">
            {/* Main Navigation */}
            <nav className="flex items-center gap-1 glass rounded-full p-1">
              {navItems.map((item) => (
                <Link
                  key={item.label}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 rounded-full transition-all duration-300",
                    isNavActive(item)
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                  )}
                >
                  <item.icon className="w-4 h-4" />
                  <span className="text-sm font-medium">{item.label}</span>
                </Link>
              ))}
            </nav>

            {/* Turbo Status Indicator */}
            {turboAvailable && (turboActive || turboReady) && (
              <div
                className={cn(
                  "flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium transition-all",
                  turboReady
                    ? "bg-green-500/10 text-green-400"
                    : "bg-yellow-500/10 text-yellow-400"
                )}
                title={turboReady ? "Turbo Mode Ready" : "Turbo Mode Warming Up"}
              >
                <span
                  className={cn(
                    "w-2 h-2 rounded-full",
                    turboReady ? "bg-green-400" : "bg-yellow-400 animate-pulse"
                  )}
                />
                <span className="hidden sm:inline">
                  {turboReady ? "Turbo" : "Warming"}
                </span>
              </div>
            )}

            {/* Settings */}
            <Link
              href="/admin"
              className={cn(
                "flex items-center justify-center w-10 h-10 rounded-full transition-all duration-300 glass",
                isSettingsActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              )}
              title="Settings"
            >
              <Settings className="w-5 h-5" />
            </Link>
          </div>
        </div>
      </div>
    </header>
  );
}
