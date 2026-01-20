"use client";

import { motion, AnimatePresence } from "framer-motion";
import { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface StatsCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  isText?: boolean;
  loading?: boolean;
}

export default function StatsCard({
  label,
  value,
  icon: Icon,
  isText = false,
  loading = false,
}: StatsCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative rounded-lg border border-border bg-card overflow-hidden"
    >
      {/* Shimmer effect - fade in/out */}
      <AnimatePresence>
        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="absolute inset-0 shimmer pointer-events-none"
          />
        )}
      </AnimatePresence>

      <div className="relative p-4 flex items-center gap-4">
        <div className="w-12 h-12 rounded-lg flex items-center justify-center bg-muted">
          <Icon className="w-6 h-6 text-foreground" />
        </div>

        <div>
          <p className="text-xs text-muted-foreground uppercase tracking-wider">
            {label}
          </p>
          <p className="text-2xl font-bold text-foreground">
            {isText ? value : typeof value === "number" ? value.toLocaleString() : value}
          </p>
        </div>
      </div>
    </motion.div>
  );
}
