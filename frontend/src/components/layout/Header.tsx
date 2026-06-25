"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Database,
  Compass,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

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
            {/* Plain <img> (not next/image) so an arbitrary external logo URL works
                without images.remotePatterns config. The origin server supplies the
                content-type, so extension-less asset URLs (e.g. /assets/<uuid>) render. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={process.env.NEXT_PUBLIC_LOGO_URL || "/logo.svg"}
              alt="Logo"
              width={45}
              height={45}
              className="h-7 w-auto"
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
