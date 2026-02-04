"use client";

import { useState } from "react";
import { PageTransition } from "@/components/layout";
import Link from "next/link";
import { motion } from "framer-motion";
import { KeyRound, LogOut, ChevronRight, Loader2 } from "lucide-react";
import { logout } from "@/lib/auth";
import { clearAdminApiKey } from "@/lib/api";

export default function AdminPage() {
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    clearAdminApiKey();
    await logout();
  };

  const adminCards = [
    {
      title: "API Keys",
      description: "Create and manage API keys for accessing the backend",
      icon: KeyRound,
      href: "/admin/api-keys",
    },
  ];

  return (
    <PageTransition>
      <div className="space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-foreground mb-2">Settings</h1>
            <p className="text-muted-foreground">
              Manage your MOCA Knowledge Base settings and API access
            </p>
          </div>
          <button
            onClick={handleLogout}
            disabled={isLoggingOut}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          >
            {isLoggingOut ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <LogOut className="w-4 h-4" />
            )}
            <span>{isLoggingOut ? "Logging out..." : "Logout"}</span>
          </button>
        </div>

        {/* Admin Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {adminCards.map((card, index) => (
            <motion.div
              key={card.title}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.1 }}
            >
              <Link href={card.href}>
                <div className="group glass glass-hover rounded-xl p-6 cursor-pointer">
                  {/* Icon */}
                  <div className="inline-flex items-center justify-center w-12 h-12 rounded-lg bg-accent/20 mb-4 group-hover:bg-accent/30 transition-colors">
                    <card.icon className="w-6 h-6 text-accent" />
                  </div>

                  {/* Content */}
                  <h2 className="text-lg font-semibold text-foreground mb-1 group-hover:text-accent transition-colors">
                    {card.title}
                  </h2>
                  <p className="text-muted-foreground text-sm">{card.description}</p>

                  {/* Arrow */}
                  <div className="flex items-center gap-2 mt-4 text-accent text-sm font-medium group-hover:gap-3 transition-all">
                    <span>Manage</span>
                    <ChevronRight className="w-4 h-4" />
                  </div>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      </div>
    </PageTransition>
  );
}
