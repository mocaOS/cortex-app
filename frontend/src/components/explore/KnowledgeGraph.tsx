"use client";

import { useRef, useCallback, useEffect, useState, useMemo } from "react";
import dynamic from "next/dynamic";
import type { GraphNode, GraphEdge, EntityDetails, GraphStats } from "@/types";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { X, ExternalLink, Loader2 } from "lucide-react";

// Internal node type for force graph
interface ForceGraphNode {
  id: string;
  label: string;
  type: string;
  description?: string;
  community_id?: number;
  mention_count: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
  [key: string]: unknown;
}

interface ForceGraphLink {
  source: string | ForceGraphNode;
  target: string | ForceGraphNode;
  type: string;
  weight?: number;  // Relationship weight (0-10)
}

// Dynamically import ForceGraph2D to avoid SSR issues
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
    </div>
  ),
}) as React.ComponentType<Record<string, unknown>>;

// Entity type colors matching Neo4j style
const TYPE_COLORS: Record<string, string> = {
  Person: "#F79767",
  Organization: "#57C7E3",
  Concept: "#DA7194",
  Technology: "#6DCE9E",
  Location: "#FFC454",
  Event: "#D9C8AE",
  Product: "#8DCC93",
  Process: "#C990C0",
  Document: "#569480",
  default: "#4C8EDA",
};

function getNodeColor(type: string): string {
  return TYPE_COLORS[type] || TYPE_COLORS.default;
}

// Calculate base node size based on mention count
function getBaseNodeSize(mentionCount: number): number {
  const base = 3;
  const scale = Math.log2(mentionCount + 1);
  return Math.min(base + scale * 1, 8);
}

interface EntityPanelProps {
  entity: ForceGraphNode | null;
  details: EntityDetails | null;
  loading: boolean;
  onClose: () => void;
}

function EntityPanel({ entity, details, loading, onClose }: EntityPanelProps) {
  if (!entity) return null;

  return (
    <div className="absolute top-4 right-4 w-96 max-h-[calc(100%-2rem)] bg-card border border-border rounded-xl shadow-2xl overflow-hidden z-10">
      <div className="flex items-center justify-between p-4 border-b border-border bg-muted/50">
        <div className="flex items-center gap-3">
          <div
            className="w-3 h-3 rounded-full"
            style={{ backgroundColor: getNodeColor(entity.type) }}
          />
          <h3 className="font-semibold text-lg truncate">{entity.label}</h3>
        </div>
        <button
          onClick={onClose}
          className="p-1 hover:bg-muted rounded-lg transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="p-4 overflow-y-auto max-h-[500px] space-y-4">
        <div className="flex items-center gap-2">
          <span
            className="px-2 py-1 text-xs font-medium rounded-full text-white"
            style={{ backgroundColor: getNodeColor(entity.type) }}
          >
            {entity.type}
          </span>
          <span className="text-sm text-muted-foreground">
            {entity.mention_count} connections
          </span>
        </div>

        {entity.description && (
          <p className="text-sm text-muted-foreground leading-relaxed">
            {entity.description}
          </p>
        )}

        {loading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {details && !loading && (
          <>
            {details.entities.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">Related Entities</h4>
                <div className="flex flex-wrap gap-1.5">
                  {details.entities.slice(0, 10).map((e, i) => (
                    <span
                      key={i}
                      className="px-2 py-1 text-xs rounded-lg bg-muted text-muted-foreground"
                      title={e.description}
                    >
                      {e.name}
                    </span>
                  ))}
                  {details.entities.length > 10 && (
                    <span className="px-2 py-1 text-xs rounded-lg bg-muted text-muted-foreground">
                      +{details.entities.length - 10} more
                    </span>
                  )}
                </div>
              </div>
            )}

            {details.relationships.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">Key Relationships</h4>
                <div className="space-y-1.5">
                  {details.relationships.slice(0, 8).map((r, i) => (
                    <div
                      key={i}
                      className="text-xs text-muted-foreground flex items-center gap-1 font-mono"
                    >
                      <span className="truncate max-w-[120px]">{r.source}</span>
                      <span className="text-accent">→</span>
                      <span className="text-accent font-medium">{r.type}</span>
                      <span className="text-accent">→</span>
                      <span className="truncate max-w-[120px]">{r.target}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {details.chunks.length > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">Mentioned In</h4>
                <div className="space-y-1.5">
                  {details.chunks.slice(0, 5).map((c, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 text-xs text-muted-foreground"
                    >
                      <ExternalLink className="w-3 h-3 flex-shrink-0" />
                      <span className="truncate">{c.filename}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        <div className="pt-2 border-t border-border">
          <p className="text-xs text-muted-foreground font-mono truncate">
            ID: {entity.id.substring(0, 12)}...
          </p>
        </div>
      </div>
    </div>
  );
}

interface LegendProps {
  types: string[];
}

function Legend({ types }: LegendProps) {
  const sortedTypes = useMemo(() => {
    return [...new Set(types)].slice(0, 10);
  }, [types]);

  return (
    <div className="absolute bottom-4 left-4 bg-card/90 backdrop-blur-sm border border-border rounded-lg p-3 z-10">
      <h4 className="text-xs font-medium text-muted-foreground mb-2">
        Entity Types
      </h4>
      <div className="flex flex-wrap gap-2 max-w-xs">
        {sortedTypes.map((type) => (
          <div key={type} className="flex items-center gap-1.5">
            <div
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: getNodeColor(type) }}
            />
            <span className="text-xs text-muted-foreground">{type}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface KnowledgeGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: GraphStats;
  className?: string;
}

// Force graph ref type
interface ForceGraphMethods {
  zoom: (k?: number, duration?: number) => number;
  centerAt: (x: number, y: number, duration?: number) => void;
  zoomToFit: (duration?: number, padding?: number) => void;
}

export default function KnowledgeGraph({
  nodes,
  edges,
  stats,
  className,
}: KnowledgeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<ForceGraphMethods | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [selectedNode, setSelectedNode] = useState<ForceGraphNode | null>(null);
  const [entityDetails, setEntityDetails] = useState<EntityDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [hoveredNode, setHoveredNode] = useState<ForceGraphNode | null>(null);

  // Convert nodes and edges to force-graph format
  const graphData = useMemo(() => {
    const forceNodes: ForceGraphNode[] = nodes.map((n) => ({
      id: n.id,
      label: n.label,
      type: n.type,
      description: n.description,
      community_id: n.community_id,
      mention_count: n.mention_count,
    }));
    
    const forceLinks: ForceGraphLink[] = edges.map((e) => ({
      source: e.source,
      target: e.target,
      type: e.type,
      weight: e.weight ?? 5.0,  // Default weight if not provided
    }));
    
    return { nodes: forceNodes, links: forceLinks };
  }, [nodes, edges]);

  // Get all unique entity types for legend
  const entityTypes = useMemo(() => {
    return nodes.map((n) => n.type);
  }, [nodes]);

  // Handle container resize
  useEffect(() => {
    if (!containerRef.current) return;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });

    resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  // Fetch entity details when a node is selected
  useEffect(() => {
    if (!selectedNode) {
      setEntityDetails(null);
      return;
    }

    const fetchDetails = async () => {
      setDetailsLoading(true);
      try {
        const details = await api.getEntityDetails(selectedNode.label);
        setEntityDetails(details);
      } catch (error) {
        console.error("Failed to fetch entity details:", error);
      } finally {
        setDetailsLoading(false);
      }
    };

    fetchDetails();
  }, [selectedNode]);

  // Node click handler
  const handleNodeClick = useCallback((node: ForceGraphNode) => {
    setSelectedNode(node);
    if (fgRef.current && node.x !== undefined && node.y !== undefined) {
      fgRef.current.centerAt(node.x, node.y, 500);
      fgRef.current.zoom(2.5, 500);
    }
  }, []);

  // Node hover handlers
  const handleNodeHover = useCallback((node: ForceGraphNode | null) => {
    setHoveredNode(node);
    if (containerRef.current) {
      containerRef.current.style.cursor = node ? "pointer" : "grab";
    }
  }, []);

  // Fix node position after dragging so it stays where you put it
  const handleNodeDragEnd = useCallback((node: ForceGraphNode) => {
    node.fx = node.x;
    node.fy = node.y;
  }, []);

  // Custom node rendering with semantic zoom (nodes shrink when zooming in)
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const label = node.label || String(node.id);
      const baseNodeSize = getBaseNodeSize(node.mention_count || 1);
      const color = getNodeColor(node.type || "default");
      const isHovered = hoveredNode?.id === node.id;
      const isSelected = selectedNode?.id === node.id;
      const x = node.x ?? 0;
      const y = node.y ?? 0;

      // SEMANTIC ZOOM: Nodes shrink as you zoom in
      // At zoom 1, show full size. As zoom increases, shrink nodes proportionally
      // This reveals more of the graph structure and labels when zoomed in
      const zoomFactor = globalScale > 1 ? Math.pow(globalScale, 0.5) : 1;
      const nodeSize = baseNodeSize / zoomFactor;
      
      // Font size scales with zoom but stays readable
      const baseFontSize = 12;
      const fontSize = Math.max(baseFontSize / globalScale, 3);
      
      // Border width scales inversely with zoom
      const borderWidth = Math.max((isSelected ? 3 : 2) / globalScale, 0.5);

      // Draw node circle
      ctx.beginPath();
      ctx.arc(x, y, nodeSize, 0, 2 * Math.PI, false);
      ctx.fillStyle = color;
      ctx.fill();

      // Draw border for hovered/selected nodes
      if (isHovered || isSelected) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = borderWidth;
        ctx.stroke();
        
        // Add glow effect
        ctx.shadowColor = color;
        ctx.shadowBlur = 10 / globalScale;
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      // Draw label - more visible when zoomed in
      const showLabel = globalScale > 0.5 || isHovered || isSelected;
      if (showLabel) {
        // Truncate long labels
        let displayLabel = label;
        if (displayLabel.length > 20 && globalScale < 2) {
          displayLabel = displayLabel.substring(0, 18) + "...";
        }
        
        ctx.font = `${isSelected ? "bold " : ""}${fontSize}px Inter, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        
        // Draw text shadow for readability
        ctx.shadowColor = "rgba(0, 0, 0, 0.8)";
        ctx.shadowBlur = 4 / globalScale;
        ctx.fillStyle = "#ffffff";
        ctx.fillText(displayLabel, x, y + nodeSize + fontSize + 1);
        ctx.shadowBlur = 0;
      }
    },
    [hoveredNode, selectedNode]
  );

  // Define pointer area for click/drag detection
  const nodePointerAreaPaint = useCallback(
    (node: ForceGraphNode, color: string, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const baseNodeSize = getBaseNodeSize(node.mention_count || 1);
      const zoomFactor = globalScale > 1 ? Math.pow(globalScale, 0.5) : 1;
      const nodeSize = baseNodeSize / zoomFactor;
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      
      // Slightly larger area for easier clicking
      ctx.beginPath();
      ctx.arc(x, y, nodeSize + 4, 0, 2 * Math.PI, false);
      ctx.fillStyle = color;
      ctx.fill();
    },
    []
  );

  // Custom link rendering
  const linkCanvasObject = useCallback(
    (link: ForceGraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const start = link.source as ForceGraphNode;
      const end = link.target as ForceGraphNode;

      if (typeof start !== "object" || typeof end !== "object") return;
      if (start.x === undefined || start.y === undefined || end.x === undefined || end.y === undefined) return;

      const startX = start.x;
      const startY = start.y;
      const endX = end.x;
      const endY = end.y;

      // Calculate node sizes for proper edge termination
      const zoomFactor = globalScale > 1 ? Math.pow(globalScale, 0.5) : 1;
      const startNodeSize = getBaseNodeSize(start.mention_count || 1) / zoomFactor;
      const endNodeSize = getBaseNodeSize(end.mention_count || 1) / zoomFactor;

      // Calculate direction
      const dx = endX - startX;
      const dy = endY - startY;
      const distance = Math.sqrt(dx * dx + dy * dy);
      
      if (distance === 0) return;

      // Normalize
      const nx = dx / distance;
      const ny = dy / distance;

      // Adjust start and end points to node edges
      const adjustedStartX = startX + nx * startNodeSize;
      const adjustedStartY = startY + ny * startNodeSize;
      const adjustedEndX = endX - nx * endNodeSize;
      const adjustedEndY = endY - ny * endNodeSize;

      // Link opacity and width - weight affects thickness
      const weight = (link as ForceGraphLink).weight ?? 5.0;
      const baseOpacity = 0.15 + (weight / 10) * 0.25;  // Higher weight = more visible
      const opacity = Math.min(baseOpacity + (globalScale - 1) * 0.1, 0.6);
      const baseLineWidth = 0.8 + (weight / 10) * 1.5;  // Weight affects line thickness
      const lineWidth = Math.max(baseLineWidth / globalScale, 0.3);

      // Draw the line
      ctx.beginPath();
      ctx.moveTo(adjustedStartX, adjustedStartY);
      ctx.lineTo(adjustedEndX, adjustedEndY);
      ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`;
      ctx.lineWidth = lineWidth;
      ctx.stroke();

      // Draw arrow when zoomed in
      if (globalScale > 1.5) {
        const arrowSize = Math.max(5 / globalScale, 2);
        const arrowAngle = Math.PI / 6;
        const angle = Math.atan2(ny, nx);
        
        ctx.beginPath();
        ctx.moveTo(adjustedEndX, adjustedEndY);
        ctx.lineTo(
          adjustedEndX - arrowSize * Math.cos(angle - arrowAngle),
          adjustedEndY - arrowSize * Math.sin(angle - arrowAngle)
        );
        ctx.moveTo(adjustedEndX, adjustedEndY);
        ctx.lineTo(
          adjustedEndX - arrowSize * Math.cos(angle + arrowAngle),
          adjustedEndY - arrowSize * Math.sin(angle + arrowAngle)
        );
        ctx.strokeStyle = `rgba(255, 255, 255, ${opacity + 0.1})`;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
      }

      // Show relationship type when very zoomed in
      if (globalScale > 3 && link.type) {
        const midX = (adjustedStartX + adjustedEndX) / 2;
        const midY = (adjustedStartY + adjustedEndY) / 2;
        const labelFontSize = Math.max(8 / globalScale, 2);
        
        ctx.font = `${labelFontSize}px Inter, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = `rgba(180, 180, 180, 0.8)`;
        ctx.fillText(link.type, midX, midY);
      }
    },
    []
  );

  return (
    <div
      ref={containerRef}
      className={cn("relative w-full h-full bg-[#0a0a0f] rounded-b-xl overflow-hidden", className)}
    >
      <ForceGraph2D
        ref={(el: unknown) => { fgRef.current = el as ForceGraphMethods | null; }}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        nodeId="id"
        nodeLabel=""
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={nodePointerAreaPaint}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        onNodeDragEnd={handleNodeDragEnd}
        onBackgroundClick={() => setSelectedNode(null)}
        cooldownTicks={100}
        warmupTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        linkDirectionalArrowLength={0}
        enableNodeDrag={true}
        enableZoomInteraction={true}
        enablePanInteraction={true}
        minZoom={0.1}
        maxZoom={10}
      />

      <Legend types={entityTypes} />

      <EntityPanel
        entity={selectedNode}
        details={entityDetails}
        loading={detailsLoading}
        onClose={() => setSelectedNode(null)}
      />

      {/* Zoom controls */}
      <div className="absolute bottom-4 right-4 flex flex-col gap-2 z-10">
        <button
          onClick={() => fgRef.current?.zoom(fgRef.current.zoom() * 1.5, 300)}
          className="w-10 h-10 bg-card/90 backdrop-blur-sm border border-border rounded-lg flex items-center justify-center text-lg font-medium hover:bg-muted transition-colors"
        >
          +
        </button>
        <button
          onClick={() => fgRef.current?.zoom(fgRef.current.zoom() / 1.5, 300)}
          className="w-10 h-10 bg-card/90 backdrop-blur-sm border border-border rounded-lg flex items-center justify-center text-lg font-medium hover:bg-muted transition-colors"
        >
          −
        </button>
        <button
          onClick={() => fgRef.current?.zoomToFit(400, 50)}
          className="w-10 h-10 bg-card/90 backdrop-blur-sm border border-border rounded-lg flex items-center justify-center text-xs font-medium hover:bg-muted transition-colors"
          title="Fit to view"
        >
          ⊡
        </button>
      </div>

      {/* Graph stats */}
      <div className="absolute top-4 left-4 bg-card/90 backdrop-blur-sm border border-border rounded-lg px-3 py-2 z-10">
        {stats ? (
          <div className="text-xs text-muted-foreground space-y-0.5">
            <p>
              <span className="font-medium text-foreground">{stats.displayed_entities}</span> of{" "}
              <span className="font-medium text-foreground">{stats.total_entities}</span> entities
              {stats.neighbor_entities_included !== undefined && stats.neighbor_entities_included > 0 && (
                <span className="text-accent"> (+{stats.neighbor_entities_included} neighbors)</span>
              )}
            </p>
            <p>
              <span className="font-medium text-foreground">{stats.displayed_relationships}</span> of{" "}
              <span className="font-medium text-foreground">{stats.total_relationships}</span> relationships
            </p>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{nodes.length}</span> entities •{" "}
            <span className="font-medium text-foreground">{edges.length}</span> relationships
          </p>
        )}
      </div>
    </div>
  );
}
