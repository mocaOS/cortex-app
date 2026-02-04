"use client";

import { usePathname } from "next/navigation";
import { Header, Footer, StatsBar } from "@/components/layout";

// Routes that should not show the header, footer, or stats bar
const authRoutes = ["/login"];

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

  // Regular pages get the full layout with header, stats, and footer
  return (
    <>
      <Header />
      <StatsBar />
      <main className="max-w-7xl mx-auto px-6 pb-12 w-full flex-1">
        {children}
      </main>
      <Footer />
    </>
  );
}
