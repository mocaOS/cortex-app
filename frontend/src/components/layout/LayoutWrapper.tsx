"use client";

import { Suspense } from "react";
import { usePathname } from "next/navigation";
import { Header, Footer, StatsBar, SubMenu } from "@/components/layout";

// Routes that should not show the header, footer, or stats bar
const authRoutes = ["/login"];

// SubMenu wrapped in Suspense for useSearchParams
function SubMenuWithSuspense() {
  return (
    <Suspense fallback={<div className="h-[52px]" />}>
      <SubMenu />
    </Suspense>
  );
}

export default function LayoutWrapper({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const isAuthRoute = authRoutes.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );

  if (isAuthRoute) {
    // Auth pages get a minimal layout
    return <>{children}</>;
  }

  const hideStatsBar = pathname.startsWith("/admin");

  // Regular pages get the full layout with header, stats, submenu, and footer
  return (
    <>
      <Header />
      {!hideStatsBar && <StatsBar />}
      <SubMenuWithSuspense />
      <main className="max-w-7xl mx-auto px-6 pt-6 pb-12 w-full flex-1">
        {children}
      </main>
      <Footer />
    </>
  );
}
