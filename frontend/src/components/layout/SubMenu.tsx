"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  FileText,
  FolderOpen,
  PenLine,
  Network,
  Layers,
  Share2,
  Users,
  Sparkles,
  MessageSquare,
  FlaskConical,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface SubMenuItem {
  href: string;
  label: string;
  icon: React.ElementType;
  param?: { key: string; value: string };
}

interface MenuSection {
  basePath: string;
  items: SubMenuItem[];
}

const menuSections: MenuSection[] = [
  {
    basePath: "/",
    items: [
      { href: "/documents", label: "Documents", icon: FileText },
      { href: "/extract", label: "Generate Graph", icon: FlaskConical },
      { href: "/collections", label: "Collections", icon: FolderOpen },
      { href: "/add", label: "Add", icon: PenLine },
    ],
  },
  {
    basePath: "/explore",
    items: [
      { href: "/explore", label: "Knowledge Graph", icon: Network, param: { key: "tab", value: "graph" } },
      { href: "/explore", label: "Entities", icon: Layers, param: { key: "tab", value: "entities" } },
      { href: "/explore", label: "Relationships", icon: Share2, param: { key: "tab", value: "relationships" } },
      { href: "/explore", label: "Communities", icon: Users, param: { key: "tab", value: "communities" } },
      { href: "/explore", label: "Deep Research", icon: Sparkles, param: { key: "tab", value: "research" } },
      { href: "/explore", label: "Chat", icon: MessageSquare, param: { key: "tab", value: "chat" } },
    ],
  },
];

// Routes that belong to the "Data" section
const dataRoutes = ["/", "/documents", "/collections", "/add", "/extract"];

export default function SubMenu() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Determine which section we're in
  const getCurrentSection = (): MenuSection | null => {
    // Check if we're in the Data section (multiple routes)
    if (dataRoutes.includes(pathname)) {
      return menuSections[0]; // Data section
    }
    // Check other sections by basePath
    for (const section of menuSections.slice(1)) {
      if (pathname.startsWith(section.basePath)) {
        return section;
      }
    }
    return null;
  };

  const currentSection = getCurrentSection();

  // Don't render submenu for pages outside our defined sections (e.g., /admin)
  if (!currentSection) {
    return null;
  }

  const isActive = (item: SubMenuItem): boolean => {
    // For Data section - match exact path
    if (dataRoutes.includes(item.href) && !item.param) {
      return pathname === item.href;
    }

    // For parameterized items (Explore tabs, Ask AI modes)
    if (item.param) {
      const currentParam = searchParams.get(item.param.key);
      // If no param in URL, check for default
      if (!currentParam) {
        // Default for explore is "graph", for ask is "chat"
        if (item.param.key === "tab" && item.param.value === "graph" && pathname === "/explore") {
          return true;
        }
        if (item.param.key === "mode" && item.param.value === "research" && pathname === "/ask") {
          return true;
        }
        return false;
      }
      return pathname === item.href && currentParam === item.param.value;
    }

    return pathname === item.href;
  };

  const getHref = (item: SubMenuItem): string => {
    if (item.param) {
      return `${item.href}?${item.param.key}=${item.param.value}`;
    }
    return item.href;
  };

  return (
    <div className="max-w-7xl mx-auto px-6 w-full">
      <nav className="flex items-center gap-1 py-2">
        {currentSection.items.map((item) => (
          <Link
            key={`${item.href}-${item.param?.value || item.label}`}
            href={getHref(item)}
            className={cn(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200",
              isActive(item)
                ? "bg-white text-black"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
