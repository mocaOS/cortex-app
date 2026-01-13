"use client";

import { motion } from "framer-motion";
import { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface StatsCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  color: "ocean" | "cyan" | "teal" | "coral";
  isText?: boolean;
}

const colorMap = {
  ocean: {
    bg: "from-ocean-500/20 to-ocean-600/10",
    border: "border-ocean-500/20",
    icon: "text-ocean-400",
    text: "text-ocean-300",
  },
  cyan: {
    bg: "from-cyan-500/20 to-cyan-600/10",
    border: "border-cyan-500/20",
    icon: "text-cyan-400",
    text: "text-cyan-300",
  },
  teal: {
    bg: "from-teal-500/20 to-teal-600/10",
    border: "border-teal-500/20",
    icon: "text-teal-400",
    text: "text-teal-300",
  },
  coral: {
    bg: "from-coral-500/20 to-coral-600/10",
    border: "border-coral-500/20",
    icon: "text-coral-400",
    text: "text-coral-300",
  },
};

export default function StatsCard({
  label,
  value,
  icon: Icon,
  color,
  isText = false,
}: StatsCardProps) {
  const colors = colorMap[color];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "relative rounded-xl border backdrop-blur-sm overflow-hidden",
        "bg-gradient-to-br",
        colors.bg,
        colors.border
      )}
    >
      {/* Shimmer effect */}
      <div className="absolute inset-0 shimmer pointer-events-none" />

      <div className="relative p-4 flex items-center gap-4">
        <div
          className={cn(
            "w-12 h-12 rounded-xl flex items-center justify-center",
            "bg-white/5"
          )}
        >
          <Icon className={cn("w-6 h-6", colors.icon)} />
        </div>

        <div>
          <p className="text-xs text-white/40 uppercase tracking-wider">
            {label}
          </p>
          <p className={cn("text-2xl font-bold", colors.text)}>
            {isText ? value : typeof value === "number" ? value.toLocaleString() : value}
          </p>
        </div>
      </div>
    </motion.div>
  );
}
