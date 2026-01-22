"use client";

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
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/", label: "Upload", icon: Upload },
  { href: "/search", label: "Search", icon: Search },
  { href: "/ask", label: "Ask AI", icon: MessageSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/collections", label: "Collections", icon: FolderOpen },
  { href: "/explore", label: "Explore", icon: Network },
];

export default function Header() {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/") {
      return pathname === "/";
    }
    return pathname.startsWith(href);
  };

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
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                )}
              >
                <item.icon className="w-4 h-4" />
                <span className="text-sm font-medium hidden sm:inline">
                  {item.label}
                </span>
              </Link>
            ))}
          </nav>
        </div>
      </div>
    </header>
  );
}
