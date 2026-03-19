"use client";

import { useRef, useCallback, useEffect, useState, useMemo } from "react";
import dynamic from "next/dynamic";
import type { GraphNode, GraphEdge, EntityDetails, GraphStats } from "@/types";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { X, ExternalLink, Loader2, Maximize2, Minimize2 } from "lucide-react";

// Internal node type for force graph
interface ForceGraphNode {
  id: string;
  label: string;
  type: string;
  description?: string;
  community_id?: number;
  mention_count: number;
  val: number;
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

// Calculate visual node radius based on mention count
function getNodeRadius(mentionCount: number): number {
  const base = 5;
  const scale = Math.log2(mentionCount + 1);
  return Math.min(base + scale * 1.5, 14);
}

// Shadow graph hit detection: the library's shadow canvas reads node.val and
// uses its own default nodeRelSize (4) to compute the hit circle radius as
// sqrt(val) * 4.  We derive val from our visual radius + padding so the
// clickable area fully covers (and slightly exceeds) the visible node.
const SHADOW_REL_SIZE = 4; // library default for shadow graph
const HIT_PADDING = 4;     // extra px around visual node for comfortable clicks

function getHitVal(mentionCount: number): number {
  const hitRadius = getNodeRadius(mentionCount) + HIT_PADDING;
  return (hitRadius / SHADOW_REL_SIZE) ** 2;
}

interface EntityPanelProps {
  entity: ForceGraphNode | null;
  details: EntityDetails | null;
  loading: boolean;
  onClose: () => void;
  onEntityNavigate?: (entityName: string) => void;
}

function EntityPanel({ entity, details, loading, onClose, onEntityNavigate }: EntityPanelProps) {
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
                    <button
                      key={i}
                      onClick={() => onEntityNavigate?.(e.name)}
                      className="px-2 py-1 text-xs rounded-lg bg-muted text-muted-foreground hover:bg-accent/20 hover:text-foreground transition-colors cursor-pointer"
                      title={e.description || `Navigate to ${e.name}`}
                    >
                      {e.name}
                    </button>
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
                      <button
                        onClick={() => onEntityNavigate?.(r.source)}
                        className="truncate max-w-[120px] hover:text-foreground transition-colors cursor-pointer"
                      >
                        {r.source}
                      </button>
                      <span className="text-accent">→</span>
                      <span className="text-accent font-medium">{r.type}</span>
                      <span className="text-accent">→</span>
                      <button
                        onClick={() => onEntityNavigate?.(r.target)}
                        className="truncate max-w-[120px] hover:text-foreground transition-colors cursor-pointer"
                      >
                        {r.target}
                      </button>
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
  initialEntity?: string | null;
}

// Force graph ref type — react-force-graph-2d exposes all kapsule methods
interface ForceGraphMethods {
  zoom: (k?: number, duration?: number) => number;
  centerAt: (x: number, y: number, duration?: number) => void;
  zoomToFit: (duration?: number, padding?: number) => void;
  screen2GraphCoords: (x: number, y: number) => { x: number; y: number };
}

const CLICK_THRESHOLD = 5; // px — distinguishes click from drag

export default function KnowledgeGraph({
  nodes,
  edges,
  stats,
  className,
  initialEntity,
}: KnowledgeGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<ForceGraphMethods | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [selectedNode, setSelectedNode] = useState<ForceGraphNode | null>(null);
  const [entityDetails, setEntityDetails] = useState<EntityDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [hoveredNode, setHoveredNode] = useState<ForceGraphNode | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Handle fullscreen toggle
  const toggleFullscreen = useCallback(async () => {
    if (!containerRef.current) return;

    try {
      if (!document.fullscreenElement) {
        await containerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (error) {
      console.error("Fullscreen toggle failed:", error);
    }
  }, []);

  // Listen for fullscreen changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  // Convert nodes and edges to force-graph format.
  // The `val` property is read by the library's shadow graph (separate instance
  // with default accessor 'val' and nodeRelSize=4) to size the invisible hit-
  // detection circles.  We inflate it so the shadow circle fully covers (and
  // slightly exceeds) the visible node, giving comfortable click/hover targets.
  const graphData = useMemo(() => {
    const forceNodes: ForceGraphNode[] = nodes.map((n) => {
      const mc = Math.max(n.mention_count || 1, 1);
      return {
        id: n.id,
        label: n.label,
        type: n.type,
        description: n.description,
        community_id: n.community_id,
        mention_count: mc,
        val: getHitVal(mc),
      };
    });

    const forceLinks: ForceGraphLink[] = edges.map((e) => ({
      source: e.source,
      target: e.target,
      type: e.type,
      weight: e.weight ?? 5.0,
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

  // Auto-select entity when navigating from Entities browser via ?entity= param
  const initialEntityConsumedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!initialEntity || initialEntity === initialEntityConsumedRef.current) return;
    if (graphData.nodes.length === 0) return;
    const node = graphData.nodes.find(
      (n) => n.label.toLowerCase() === initialEntity.toLowerCase()
    );
    if (node) {
      initialEntityConsumedRef.current = initialEntity;
      // Delay slightly so the force graph has initialized and node positions exist
      setTimeout(() => {
        setSelectedNode(node);
        if (fgRef.current && node.x !== undefined && node.y !== undefined) {
          fgRef.current.centerAt(node.x, node.y, 500);
          fgRef.current.zoom(2.5, 500);
        }
      }, 300);
    }
  }, [initialEntity, graphData.nodes]);

  // Fix node position after dragging so it stays where you put it
  const handleNodeDragEnd = useCallback((node: ForceGraphNode) => {
    node.fx = node.x;
    node.fy = node.y;
  }, []);

  // ---------- Geometric pointer detection (Brave fingerprinting fallback) ----------
  // Brave randomizes getImageData() to block canvas fingerprinting, which breaks
  // force-graph's shadow-canvas color-picking.  We supplement with geometric
  // distance checking so hover/click works on ALL browsers.

  const findNodeAtCoords = useCallback((gx: number, gy: number): ForceGraphNode | null => {
    let closest: ForceGraphNode | null = null;
    let closestDist = Infinity;
    for (const node of graphData.nodes) {
      if (node.x === undefined || node.y === undefined) continue;
      const dx = gx - node.x;
      const dy = gy - node.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const hitRadius = getNodeRadius(node.mention_count || 1) + HIT_PADDING;
      if (dist < hitRadius && dist < closestDist) {
        closest = node;
        closestDist = dist;
      }
    }
    return closest;
  }, [graphData.nodes]);

  // Track the last geometrically-hovered node id to avoid redundant state updates
  const geoHoveredIdRef = useRef<string | null>(null);

  // Convert a PointerEvent to graph coordinates via the library's screen2GraphCoords
  const toGraphCoords = useCallback((e: PointerEvent): { x: number; y: number } | null => {
    const fg = fgRef.current;
    if (!fg?.screen2GraphCoords || !containerRef.current) return null;
    const canvas = containerRef.current.querySelector("canvas");
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    return fg.screen2GraphCoords(e.clientX - rect.left, e.clientY - rect.top);
  }, []);

  // Pointer events for geometric hover + click fallback
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let downNode: ForceGraphNode | null = null;
    let downX = 0;
    let downY = 0;

    const onPointerMove = (e: PointerEvent) => {
      const coords = toGraphCoords(e);
      if (!coords) return;
      const node = findNodeAtCoords(coords.x, coords.y);
      const nodeId = node?.id ?? null;
      if (nodeId !== geoHoveredIdRef.current) {
        geoHoveredIdRef.current = nodeId;
        setHoveredNode(node);
        // Set cursor on the canvas itself so it isn't overridden by the library
        const canvas = container.querySelector("canvas");
        if (canvas) (canvas as HTMLElement).style.cursor = node ? "pointer" : "grab";
      }
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      downX = e.clientX;
      downY = e.clientY;
      const coords = toGraphCoords(e);
      downNode = coords ? findNodeAtCoords(coords.x, coords.y) : null;
    };

    const onPointerUp = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const dx = e.clientX - downX;
      const dy = e.clientY - downY;
      const isClick = Math.sqrt(dx * dx + dy * dy) < CLICK_THRESHOLD;

      if (isClick) {
        if (downNode) {
          handleNodeClick(downNode);
        } else {
          setSelectedNode(null);
        }
      }
      downNode = null;
    };

    container.addEventListener("pointermove", onPointerMove);
    container.addEventListener("pointerdown", onPointerDown);
    container.addEventListener("pointerup", onPointerUp);
    return () => {
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerdown", onPointerDown);
      container.removeEventListener("pointerup", onPointerUp);
    };
  }, [toGraphCoords, findNodeAtCoords, handleNodeClick]);

  // Custom node rendering (replace mode — replaces the default circle on the
  // MAIN canvas only; the shadow graph has its own default renderer that draws
  // correctly-sized circles using node.val + __indexColor for hit detection)
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const label = node.label || String(node.id);
      const nodeSize = getNodeRadius(node.mention_count || 1);
      const color = getNodeColor(node.type || "default");
      const isHovered = hoveredNode?.id === node.id;
      const isSelected = selectedNode?.id === node.id;
      const x = node.x ?? 0;
      const y = node.y ?? 0;

      // Font size scales with zoom but stays readable
      const fontSize = Math.max(12 / globalScale, 3);

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

  // Navigate to entity by name (used by EntityPanel clicks)
  const handleEntityNavigate = useCallback((entityName: string) => {
    const node = graphData.nodes.find(
      (n) => n.label.toLowerCase() === entityName.toLowerCase()
    );
    if (node) {
      handleNodeClick(node);
    }
  }, [graphData.nodes, handleNodeClick]);

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
      const startNodeSize = getNodeRadius(start.mention_count || 1);
      const endNodeSize = getNodeRadius(end.mention_count || 1);

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
        linkCanvasObject={linkCanvasObject}
        onNodeDragEnd={handleNodeDragEnd}
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
        autoPauseRedraw={false}
      />

      <Legend types={entityTypes} />

      <EntityPanel
        entity={selectedNode}
        details={entityDetails}
        loading={detailsLoading}
        onClose={() => setSelectedNode(null)}
        onEntityNavigate={handleEntityNavigate}
      />

      {/* Zoom controls */}
      <div className="absolute bottom-4 right-4 flex flex-col gap-2 z-10">
        <button
          onClick={toggleFullscreen}
          className="w-10 h-10 bg-card/90 backdrop-blur-sm border border-border rounded-lg flex items-center justify-center hover:bg-muted transition-colors"
          title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
        >
          {isFullscreen ? (
            <Minimize2 className="w-4 h-4" />
          ) : (
            <Maximize2 className="w-4 h-4" />
          )}
        </button>
        <div className="h-px bg-border" />
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
