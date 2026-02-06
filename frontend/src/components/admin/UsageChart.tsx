"use client";

import { useMemo } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  Area,
  AreaChart,
} from "recharts";
import { Activity, AlertTriangle } from "lucide-react";
import type { APIKeyUsageDataPoint } from "@/types";

// Chart color palette
const COLORS = [
  "#f59e0b", // amber
  "#10b981", // emerald
  "#3b82f6", // blue
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#f97316", // orange
  "#84cc16", // lime
];

// Axis text color (matches muted-foreground for dark theme)
const AXIS_COLOR = "#a1a1aa"; // zinc-400 - readable on dark backgrounds
const GRID_COLOR = "#27272a"; // zinc-800 - subtle grid lines

// Custom tooltip component
const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; name: string; color: string }>; label?: string }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-popover/95 backdrop-blur-sm border border-border rounded-lg shadow-xl p-3 min-w-[140px]">
        <p className="text-xs font-medium text-muted-foreground mb-2">{label}</p>
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <div 
                className="w-2 h-2 rounded-full" 
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-xs text-muted-foreground">{entry.name}</span>
            </div>
            <span className="text-sm font-semibold text-foreground tabular-nums">
              {entry.value.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

interface UsageLineChartProps {
  data: APIKeyUsageDataPoint[];
  height?: number;
}

export function UsageLineChart({ data, height = 200 }: UsageLineChartProps) {
  const formattedData = useMemo(() => {
    return data.map((item) => ({
      ...item,
      // Format date for display
      displayDate: new Date(item.date).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      }),
    }));
  }, [data]);

  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 text-muted-foreground" style={{ height }}>
        <Activity className="w-8 h-8 opacity-40" />
        <span className="text-sm">No usage data available</span>
      </div>
    );
  }

  return (
    <div className="relative">
      {/* Legend */}
      <div className="absolute top-0 right-0 flex items-center gap-4 text-xs">
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
          <span className="text-muted-foreground">Requests</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-rose-500" />
          <span className="text-muted-foreground">Errors</span>
        </div>
      </div>
      
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={formattedData} margin={{ top: 25, right: 5, left: -10, bottom: 5 }}>
          <defs>
            <linearGradient id="requestsGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="errorsGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f43f5e" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#f43f5e" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid 
            strokeDasharray="3 3" 
            stroke={GRID_COLOR}
            opacity={0.5}
            vertical={false}
          />
          <XAxis
            dataKey="displayDate"
            stroke={AXIS_COLOR}
            tick={{ fill: AXIS_COLOR }}
            fontSize={11}
            tickLine={false}
            axisLine={false}
            dy={10}
            tickMargin={8}
          />
          <YAxis
            stroke={AXIS_COLOR}
            tick={{ fill: AXIS_COLOR }}
            fontSize={11}
            tickLine={false}
            axisLine={false}
            width={35}
            tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(0)}k` : value}
          />
          <Tooltip content={<CustomTooltip />} />
          <Area
            type="monotone"
            dataKey="requests"
            stroke="#f59e0b"
            strokeWidth={2}
            fill="url(#requestsGradient)"
            dot={false}
            activeDot={{ r: 4, fill: "#f59e0b", stroke: "#fff", strokeWidth: 2 }}
            name="Requests"
          />
          <Area
            type="monotone"
            dataKey="errors"
            stroke="#f43f5e"
            strokeWidth={2}
            fill="url(#errorsGradient)"
            dot={false}
            activeDot={{ r: 4, fill: "#f43f5e", stroke: "#fff", strokeWidth: 2 }}
            name="Errors"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

interface EndpointBreakdownChartProps {
  data: Record<string, number>;
  height?: number;
}

export function EndpointBreakdownChart({ data, height = 200 }: EndpointBreakdownChartProps) {
  const chartData = useMemo(() => {
    const entries = Object.entries(data);
    if (entries.length === 0) return [];
    
    return entries
      .map(([name, value]) => ({
        name: name.charAt(0).toUpperCase() + name.slice(1),
        value,
      }))
      .sort((a, b) => b.value - a.value);
  }, [data]);

  if (chartData.length === 0) {
    return (
      <div className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
        No endpoint data available
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          innerRadius={40}
          outerRadius={70}
          paddingAngle={2}
          dataKey="value"
          label={({ name, percent }) => `${name} (${((percent ?? 0) * 100).toFixed(0)}%)`}
          labelLine={false}
        >
          {chartData.map((_, index) => (
            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            backgroundColor: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: "8px",
            fontSize: "12px",
          }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

interface EndpointBarChartProps {
  data: Record<string, number>;
  height?: number;
}

// Custom bar tooltip
const BarTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ value: number; payload: { name: string } }> }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-popover/95 backdrop-blur-sm border border-border rounded-lg shadow-xl px-3 py-2">
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">{payload[0].payload.name}</span>
          <span className="text-sm font-semibold text-foreground tabular-nums">
            {payload[0].value.toLocaleString()}
          </span>
        </div>
      </div>
    );
  }
  return null;
};

export function EndpointBarChart({ data, height = 200 }: EndpointBarChartProps) {
  const chartData = useMemo(() => {
    const entries = Object.entries(data);
    if (entries.length === 0) return [];
    
    return entries
      .map(([name, value], index) => ({
        name: name.charAt(0).toUpperCase() + name.slice(1),
        requests: value,
        fill: COLORS[index % COLORS.length],
      }))
      .sort((a, b) => b.requests - a.requests)
      .slice(0, 8); // Top 8 endpoints
  }, [data]);

  if (chartData.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 text-muted-foreground" style={{ height }}>
        <AlertTriangle className="w-8 h-8 opacity-40" />
        <span className="text-sm">No endpoint data available</span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart 
        data={chartData} 
        layout="vertical" 
        margin={{ top: 5, right: 15, left: 0, bottom: 5 }}
        barCategoryGap="20%"
      >
        <CartesianGrid 
          strokeDasharray="3 3" 
          stroke={GRID_COLOR}
          opacity={0.5} 
          horizontal={false} 
        />
        <XAxis
          type="number"
          stroke={AXIS_COLOR}
          tick={{ fill: AXIS_COLOR }}
          fontSize={11}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => value >= 1000 ? `${(value / 1000).toFixed(0)}k` : value}
        />
        <YAxis
          type="category"
          dataKey="name"
          stroke={AXIS_COLOR}
          tick={{ fill: AXIS_COLOR }}
          fontSize={11}
          tickLine={false}
          axisLine={false}
          width={60}
        />
        <Tooltip content={<BarTooltip />} cursor={{ fill: 'hsl(var(--muted))', opacity: 0.3 }} />
        <Bar 
          dataKey="requests" 
          radius={[0, 6, 6, 0]}
        >
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
