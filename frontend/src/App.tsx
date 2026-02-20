import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  type MouseEvent,
} from "react";
import * as d3 from "d3";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { useCopilotReadable, useCopilotAction } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";
import { CheckCircle2, ShieldAlert, ActivitySquare, ServerCrash, Lightbulb, TrendingUp, AlertTriangle, Eye, CheckCheck, Network, Cpu, Zap, ArrowUpCircle, ArrowDownCircle, FileText, Play, BarChart3 } from "lucide-react";

// ─── Error Boundary for CopilotKit ──────────────────────────────────────────
class CopilotErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: "" };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 20, color: "#f5a623", fontFamily: "'DM Mono', monospace", fontSize: 11, background: "#0a1018", minHeight: "100vh" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "80vh", gap: 16 }}>
            <div style={{ fontSize: 32 }}>⚠️</div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 14, color: "#c8daf0", marginBottom: 8 }}>Agent Backend Unavailable</div>
              <div style={{ color: "#6a8aa0", maxWidth: 400 }}>Start the backend with <code style={{ background: "#1a2a3a", padding: "2px 6px", borderRadius: 3 }}>python -m uvicorn api.main:app --reload --port 8000</code></div>
            </div>
            <button onClick={() => { this.setState({ hasError: false }); window.location.reload(); }}
              style={{ marginTop: 12, padding: "8px 24px", background: "#7b61ff", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontFamily: "'DM Mono', monospace", fontSize: 11 }}>
              Retry Connection
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

type Health = "healthy" | "degraded" | "critical" | "rolling";
type NodeType = "gateway" | "service" | "database" | "cache" | "queue" | "storage";
type RemediationState = "scaling" | "rolling";

type AgentAnnotation = { id: string; text: string; ts: string };
type ActionLog = { action_type: string; service: string; result: string; timestamp?: string };

type ServiceNode = {
  id: string;
  label: string;
  type: NodeType;
  health: Health;
  cpu: number;
  mem: number;
  rpm: number;
  error_rate: number;
  replicas: number;
};

type NodeDatum = ServiceNode & {
  index?: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
};

type LinkEndpoint = string | NodeDatum;
type GraphLink = { source: LinkEndpoint; target: LinkEndpoint };
type DragEventLike = { active: boolean; x: number; y: number };

// ─── API HELPERS ─────────────────────────────────────────────────────────────

function mapHealth(score: number | null | undefined): Health {
  if (score == null) return "healthy";
  if (score >= 80) return "healthy";
  if (score >= 50) return "degraded";
  return "critical";
}

const VALID_TYPES = new Set(["gateway", "service", "database", "cache", "queue", "storage"]);
function mapType(raw: string | null | undefined): NodeType {
  if (raw && VALID_TYPES.has(raw)) return raw as NodeType;
  return "service";
}

// Normalize latency metrics to 0-100 for display
// p99 baseline ~200ms healthy, ~4000ms critical
function latencyToCpu(p99: number | null | undefined): number {
  return Math.min(100, Math.round((p99 ?? 200) / 40));
}
// avg baseline ~80ms healthy, ~1400ms critical
function latencyToMem(avg: number | null | undefined): number {
  return Math.min(100, Math.round((avg ?? 100) / 14));
}

// ─── FALLBACK STATIC DATA (used when backend is unreachable) ─────────────────

const FALLBACK_NODES: ServiceNode[] = [
  { id: "api-gateway", label: "API Gateway", type: "gateway", health: "healthy", cpu: 12, mem: 34, rpm: 4300, error_rate: 0.1, replicas: 2 },
  { id: "auth-service", label: "Auth Service", type: "service", health: "healthy", cpu: 45, mem: 61, rpm: 2100, error_rate: 0.5, replicas: 3 },
  { id: "order-service", label: "Order Service", type: "service", health: "critical", cpu: 94, mem: 87, rpm: 560, error_rate: 15.4, replicas: 1 },
  { id: "inventory-svc", label: "Inventory", type: "service", health: "degraded", cpu: 71, mem: 73, rpm: 1200, error_rate: 4.2, replicas: 2 },
  { id: "payment-service", label: "Payment Service", type: "service", health: "healthy", cpu: 28, mem: 45, rpm: 450, error_rate: 0.0, replicas: 3 },
  { id: "notification-svc", label: "Notifications", type: "service", health: "healthy", cpu: 18, mem: 29, rpm: 800, error_rate: 0.1, replicas: 2 },
  { id: "postgres-main", label: "PostgreSQL", type: "database", health: "healthy", cpu: 33, mem: 68, rpm: 9000, error_rate: 0.0, replicas: 1 },
  { id: "redis-cache", label: "Redis Cache", type: "cache", health: "degraded", cpu: 55, mem: 82, rpm: 15000, error_rate: 1.2, replicas: 1 },
  { id: "kafka", label: "Kafka", type: "queue", health: "healthy", cpu: 22, mem: 44, rpm: 20000, error_rate: 0.0, replicas: 3 },
  { id: "s3-storage", label: "S3 Bucket", type: "storage", health: "healthy", cpu: 5, mem: 10, rpm: 300, error_rate: 0.0, replicas: 1 },
];

const FALLBACK_LINKS: Array<{ source: string; target: string }> = [
  { source: "api-gateway", target: "auth-service" },
  { source: "api-gateway", target: "order-service" },
  { source: "api-gateway", target: "inventory-svc" },
  { source: "order-service", target: "payment-service" },
  { source: "order-service", target: "notification-svc" },
  { source: "order-service", target: "postgres-main" },
  { source: "order-service", target: "redis-cache" },
  { source: "inventory-svc", target: "postgres-main" },
  { source: "inventory-svc", target: "kafka" },
  { source: "payment-service", target: "postgres-main" },
  { source: "notification-svc", target: "kafka" },
  { source: "order-service", target: "s3-storage" },
  { source: "auth-service", target: "redis-cache" },
];

// ─── CONSTANTS ────────────────────────────────────────────────────────────────

const HEALTHS: Health[] = ["healthy", "degraded", "critical", "rolling"];

const HEALTH_COLORS: Record<Health, string> = {
  healthy: "#00e5a0",
  degraded: "#f5a623",
  critical: "#ff3b5c",
  rolling: "#7b61ff",
};

const TYPE_ICONS: Record<NodeType, string> = {
  gateway: "⬡",
  service: "◈",
  database: "⬢",
  cache: "◇",
  queue: "⬟",
  storage: "▣",
};

// ─── COMPONENT ───────────────────────────────────────────────────────────────

export default function DeployOpsCenter() {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<{ stop: () => void } | null>(null);
  // Preserve D3 node positions across graph re-renders to avoid layout jumps
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});

  const [nodes, setNodes] = useState<ServiceNode[]>(FALLBACK_NODES.map(n => ({ ...n })));
  const [links, setLinks] = useState<Array<{ source: string; target: string }>>(FALLBACK_LINKS);
  const [selectedNode, setSelectedNode] = useState<NodeDatum | null>(null);

  const [agentAnnotations, setAgentAnnotations] = useState<AgentAnnotation[]>([]);
  const [remediationFeed, setRemediationFeed] = useState<ActionLog[]>([]);
  const [validationResult, setValidationResult] = useState<{ service: string, passed: boolean, msg: string }>({ service: "order-service", passed: true, msg: "TestSprite Validation Passed" });

  const [activeRemediations, setActiveRemediations] = useState<Record<string, RemediationState>>({});
  const [apiStatus, setApiStatus] = useState<"loading" | "connected" | "offline">("loading");

  const [insightsData, setInsightsData] = useState<any>(null);
  const [showInsights, setShowInsights] = useState(false);

  // Insights panel state
  const [rightTab, setRightTab] = useState<"agent" | "insights" | "scaling">("agent");
  const [allInsights, setAllInsights] = useState<any[]>([]);
  const [allPatterns, setAllPatterns] = useState<any[]>([]);
  const [allRecommendations, setAllRecommendations] = useState<any[]>([]);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [generatingInsights, setGeneratingInsights] = useState(false);

  // Cluster panel state
  const [clusterStatus, setClusterStatus] = useState<any>(null);
  const [clusterEvents, setClusterEvents] = useState<any[]>([]);
  const [simulatingLoad, setSimulatingLoad] = useState(false);

  // Scaling dashboard state
  const [scaleReport, setScaleReport] = useState<any>(null);
  const [scalingInProgress, setScalingInProgress] = useState(false);
  const [validationResults, setValidationResults] = useState<any[]>([]);
  const [showScaleReport, setShowScaleReport] = useState(false);

  // Agent live activity feed
  const [agentActivity, setAgentActivity] = useState<any[]>([]);
  // Tracked via ref so the polling useEffect doesn't re-mount on every new activity
  const activitySinceIdRef = useRef(0);

  // Ref so D3 click handlers always call the latest version of handleNodeClick
  // without stale closures from the useEffect capture.
  const nodeClickRef = useRef<(d: NodeDatum) => void>(() => { });

  // In-flight guards — prevent overlapping concurrent fetches
  const insightsFetchingRef = useRef(false);
  const clusterFetchingRef = useRef(false);

  // Page-visibility — pause all polling when the browser tab is hidden
  const isPageVisibleRef = useRef(true);
  useEffect(() => {
    const onVisibility = () => { isPageVisibleRef.current = !document.hidden; };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // ── Initial graph load ────────────────────────────────────────────────────
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/graph/");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const mappedNodes: ServiceNode[] = (data.nodes as any[]).map(n => ({
          id: n.id,
          label: n.label ?? n.id,
          type: mapType(n.type),
          health: mapHealth(n.health_score),
          cpu: n.cpu_usage_percent ?? latencyToCpu(n.p99_latency_ms),
          mem: n.mem_usage_percent ?? latencyToMem(n.avg_latency_ms),
          rpm: n.rpm ?? Math.round(Math.random() * 5000 + 100),
          error_rate: n.error_rate_percent ?? 0.0,
          replicas: 1,
        }));

        const mappedLinks = (data.links as any[]).map(l => ({
          source: l.source,
          target: l.target,
        }));

        setNodes(mappedNodes);
        setLinks(mappedLinks);
        setApiStatus("connected");
      } catch {
        setApiStatus("offline");
      }
    };
    load();
  }, []);

  // ── Deployment annotations ────────────────────────────────────────────────
  useEffect(() => {
    if (apiStatus !== "connected") return;
    fetch("/api/graph/deployments/recent?hours=12")
      .then(r => r.json())
      .then(data => {
        const annotations: AgentAnnotation[] = (data.deployments as any[])
          .slice(0, 5)
          .map(d => ({
            id: d.service,
            text: `deployed ${d.version ?? "?"} — ${d.status ?? "unknown"}`,
            ts: new Date(d.deployed_at).toLocaleTimeString(),
          }));
        if (annotations.length > 0) setAgentAnnotations(annotations);
      })
      .catch(() => { });

    // Fetch Remediation Feed
    fetch("/api/actions/")
      .then(r => r.json())
      .then(data => {
        if (data.actions && data.actions.length > 0) {
          setRemediationFeed(data.actions.map((a: any) => ({ ...a, timestamp: new Date().toLocaleTimeString() })));
        }
      })
      .catch(() => { });
  }, [apiStatus]);

  // ── Health polling — 5 s interval ────────────────────────────────────────
  useEffect(() => {
    if (apiStatus !== "connected") return;
    const poll = async () => {
      if (!isPageVisibleRef.current) return;
      try {
        const res = await fetch("/api/agent/health");
        if (!res.ok) { console.warn("[health] poll failed:", res.status); return; }
        const data = await res.json();
        setNodes(prev =>
          prev.map(n => {
            const u = (data.services as any[]).find(s => s.service === n.id);
            if (!u) return n;
            return {
              ...n,
              health: mapHealth(u.health_score),
              cpu: u.cpu_usage_percent ?? latencyToCpu(u.p99_latency_ms),
              mem: u.mem_usage_percent ?? latencyToMem(u.avg_latency_ms),
              rpm: u.rpm ?? n.rpm,
              error_rate: u.error_rate_percent ?? n.error_rate,
            };
          })
        );
      } catch (err) {
        console.warn("[health] poll error:", err);
      }
    };
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [apiStatus]);

  // ── Insights data fetching ───────────────────────────────────────────────
  const fetchInsightsData = useCallback(async () => {
    if (apiStatus !== "connected" || insightsFetchingRef.current || !isPageVisibleRef.current) return;
    insightsFetchingRef.current = true;
    setInsightsLoading(true);
    try {
      // allSettled so one slow/failing endpoint doesn't drop the others
      const [insRes, patRes, recRes] = await Promise.allSettled([
        fetch("/api/insights/").then(r => { if (!r.ok) throw new Error(`insights ${r.status}`); return r.json(); }),
        fetch("/api/insights/patterns").then(r => { if (!r.ok) throw new Error(`patterns ${r.status}`); return r.json(); }),
        fetch("/api/insights/recommendations").then(r => { if (!r.ok) throw new Error(`recs ${r.status}`); return r.json(); }),
      ]);
      if (insRes.status === "fulfilled") setAllInsights(insRes.value.insights || []);
      else console.warn("[insights] fetch failed:", insRes.reason);
      if (patRes.status === "fulfilled") setAllPatterns(patRes.value.patterns || []);
      else console.warn("[patterns] fetch failed:", patRes.reason);
      if (recRes.status === "fulfilled") setAllRecommendations(recRes.value.recommendations || []);
      else console.warn("[recommendations] fetch failed:", recRes.reason);
    } finally {
      insightsFetchingRef.current = false;
      setInsightsLoading(false);
    }
  }, [apiStatus]);

  useEffect(() => {
    if (rightTab === "insights" || rightTab === "agent") fetchInsightsData();
  }, [rightTab, fetchInsightsData]);

  // Poll insights every 15s when insights or agent tab is active
  useEffect(() => {
    if ((rightTab !== "insights" && rightTab !== "agent") || apiStatus !== "connected") return;
    const id = setInterval(fetchInsightsData, 15000);
    return () => clearInterval(id);
  }, [rightTab, apiStatus, fetchInsightsData]);

  // Poll agent activity feed every 3s on agent tab
  // activitySinceIdRef is a ref — not in deps — so the interval never restarts on new activity
  useEffect(() => {
    if (rightTab !== "agent" || apiStatus !== "connected") return;
    const fetchActivity = async () => {
      if (!isPageVisibleRef.current) return;
      try {
        const res = await fetch(`/api/agent/activity?since_id=${activitySinceIdRef.current}&limit=30`);
        if (!res.ok) { console.warn("[activity] fetch failed:", res.status); return; }
        const data = await res.json();
        if (data.activity && data.activity.length > 0) {
          setAgentActivity((prev: any[]) => {
            const merged = [...data.activity, ...prev];
            const seen = new Set();
            return merged.filter((a: any) => {
              if (seen.has(a.id)) return false;
              seen.add(a.id);
              return true;
            }).slice(0, 50);
          });
          // Safe id extraction — filter out undefined/non-numeric ids before Math.max
          const validIds: number[] = data.activity
            .map((a: any) => a.id)
            .filter((id: any) => typeof id === "number" && !isNaN(id));
          if (validIds.length > 0) {
            activitySinceIdRef.current = Math.max(...validIds);
          }
        }
      } catch (err) {
        console.warn("[activity] fetch error:", err);
      }
    };
    fetchActivity();
    const id = setInterval(fetchActivity, 3000);
    return () => clearInterval(id);
  }, [rightTab, apiStatus]); // activitySinceIdRef intentionally excluded — it's a ref

  const handleGenerateInsights = async (serviceName?: string) => {
    setGeneratingInsights(true);
    try {
      await fetch("/api/insights/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service_name: serviceName || null }),
      });
      await fetchInsightsData();
    } catch { }
    setGeneratingInsights(false);
  };

  const handleAcknowledgeInsight = async (insightId: string) => {
    try {
      await fetch(`/api/insights/${insightId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "acknowledged" }),
      });
      fetchInsightsData();
    } catch { }
  };

  const handleResolveInsight = async (insightId: string) => {
    try {
      await fetch(`/api/insights/${insightId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "resolved" }),
      });
      fetchInsightsData();
    } catch { }
  };

  // ── Cluster data fetching ────────────────────────────────────────────────
  const fetchClusterStatus = useCallback(async () => {
    if (apiStatus !== "connected" || clusterFetchingRef.current || !isPageVisibleRef.current) return;
    clusterFetchingRef.current = true;
    try {
      const [statusRes, eventsRes] = await Promise.allSettled([
        fetch("/api/cluster/status").then(r => { if (!r.ok) throw new Error(`status ${r.status}`); return r.json(); }),
        fetch("/api/cluster/events").then(r => { if (!r.ok) throw new Error(`events ${r.status}`); return r.json(); }),
      ]);
      if (statusRes.status === "fulfilled") setClusterStatus(statusRes.value);
      else console.warn("[cluster/status] fetch failed:", statusRes.reason);
      if (eventsRes.status === "fulfilled") setClusterEvents(eventsRes.value.events || []);
      else console.warn("[cluster/events] fetch failed:", eventsRes.reason);
    } finally {
      clusterFetchingRef.current = false;
    }
  }, [apiStatus]);

  useEffect(() => {
    if (rightTab === "insights") fetchClusterStatus();
  }, [rightTab, fetchClusterStatus]);

  // Poll cluster every 5s when insights tab is active
  useEffect(() => {
    if (rightTab !== "insights" || apiStatus !== "connected") return;
    const id = setInterval(fetchClusterStatus, 5000);
    return () => clearInterval(id);
  }, [rightTab, apiStatus, fetchClusterStatus]);

  const handleSimulateLoad = async () => {
    setSimulatingLoad(true);
    try {
      await fetch("/api/cluster/simulate-load", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 6 }),
      });
      // Run a few ticks so the demo shows scaling
      for (let i = 0; i < 3; i++) {
        await new Promise(r => setTimeout(r, 500));
        await fetch("/api/cluster/tick", { method: "POST" });
      }
      await fetchClusterStatus();
    } catch { }
    setSimulatingLoad(false);
  };

  const handleMapeKTick = async () => {
    try {
      await fetch("/api/cluster/tick", { method: "POST" });
      await fetchClusterStatus();
    } catch { }
  };

  // ── Scaling handlers ────────────────────────────────────────────────────
  const handleManualScale = async (direction: "up" | "down") => {
    setScalingInProgress(true);
    try {
      const res = await fetch("/api/cluster/scale", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ direction, reason: "user_initiated" }),
      });
      if (res.ok) {
        await fetchClusterStatus();
        await fetchScaleReport();
      }
    } catch { }
    setScalingInProgress(false);
  };

  const fetchScaleReport = async () => {
    if (!isPageVisibleRef.current) return;
    try {
      const res = await fetch("/api/cluster/report");
      if (res.ok) { setScaleReport(await res.json()); }
      else console.warn("[scale/report] fetch failed:", res.status);
    } catch (err) {
      console.warn("[scale/report] fetch error:", err);
    }
  };

  const fetchValidations = async () => {
    if (!isPageVisibleRef.current) return;
    try {
      const res = await fetch("/api/cluster/validations");
      if (res.ok) {
        const data = await res.json();
        setValidationResults(data.validations || []);
      } else {
        console.warn("[cluster/validations] fetch failed:", res.status);
      }
    } catch (err) {
      console.warn("[cluster/validations] fetch error:", err);
    }
  };

  const handleRunValidation = async () => {
    try {
      await fetch("/api/cluster/validate", { method: "POST" });
      await fetchValidations();
    } catch { }
  };

  useEffect(() => {
    if (rightTab === "scaling") {
      fetchScaleReport();
      fetchValidations();
    }
  }, [rightTab]);

  // Poll scaling tab every 3s
  useEffect(() => {
    if (rightTab !== "scaling" || apiStatus !== "connected") return;
    const id = setInterval(() => { fetchScaleReport(); fetchValidations(); fetchClusterStatus(); }, 3000);
    return () => clearInterval(id);
  }, [rightTab, apiStatus]);

  // ── Node click handler (kept current via ref, called from D3) ────────────
  const handleNodeClick = useCallback(
    async (d: NodeDatum) => {
      setSelectedNode(d);

      // Fetch deep insights when node is selected
      fetch(`/api/insights/${d.id}`)
        .then(res => res.json())
        .then(data => setInsightsData(data))
        .catch(console.error);

      if (apiStatus !== "connected") return;

      // Immediately show analysis-in-progress annotation
      const ts = new Date().toLocaleTimeString();
      setAgentAnnotations(prev => [
        { id: d.id, text: "Running agent analysis…", ts },
        ...prev.filter(a => a.id !== d.id),
      ]);

      try {
        const res = await fetch("/api/agent/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ service: d.id, trigger: "manual" }),
        });
        if (!res.ok) return;
        const report = await res.json();

        const summary =
          report.chat_summary ??
          report.recommended_action ??
          `health_score: ${report.health_score ?? "?"}`;

        setAgentAnnotations(prev => [
          { id: d.id, text: summary, ts: new Date().toLocaleTimeString() },
          ...prev.filter(a => a.id !== d.id),
        ]);

        // Reflect updated health from analysis result
        if (report.health_score != null) {
          setNodes(prev =>
            prev.map(n =>
              n.id === d.id ? { ...n, health: mapHealth(report.health_score) } : n
            )
          );
        }
      } catch { }
    },
    [apiStatus]
  );

  // Keep ref current
  useEffect(() => {
    nodeClickRef.current = handleNodeClick;
  }, [handleNodeClick]);

  // ── D3 force graph ────────────────────────────────────────────────────────
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const W = el.clientWidth || 580;
    const H = el.clientHeight || 520;

    d3.select(el).selectAll("*").remove();

    const svg = d3.select(el)
      .attr("viewBox", `0 0 ${W} ${H}`)
      .style("background", "transparent");

    // Glow filters per health state
    const defs = svg.append("defs");
    HEALTHS.forEach(h => {
      const f = defs.append("filter")
        .attr("id", `glow-${h}`)
        .attr("x", "-50%").attr("y", "-50%")
        .attr("width", "200%").attr("height", "200%");
      f.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "blur");
      const merge = f.append("feMerge");
      merge.append("feMergeNode").attr("in", "blur");
      merge.append("feMergeNode").attr("in", "SourceGraphic");
    });

    const linkData: GraphLink[] = links.map(l => ({ source: l.source, target: l.target }));

    // Restore previous positions to avoid layout jumps on health updates
    const nodeData: NodeDatum[] = nodes.map(n => {
      const saved = positionsRef.current[n.id];
      return {
        ...n,
        x: saved?.x ?? (W / 2 + (Math.random() - 0.5) * 200),
        y: saved?.y ?? (H / 2 + (Math.random() - 0.5) * 200),
      };
    });

    const sim = d3.forceSimulation(nodeData)
      .force("link", d3.forceLink(linkData).id((d: NodeDatum) => d.id).distance(110).strength(0.7))
      .force("charge", d3.forceManyBody().strength(-380))
      .force("center", d3.forceCenter(W / 2, H / 2))
      .force("collision", d3.forceCollide(48));

    simRef.current = sim;

    // Links
    const linkSel = svg.append("g").selectAll("line")
      .data(linkData).enter().append("line")
      .attr("stroke", "#1e2a3a")
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.7);

    // Pulse overlay for critical paths (any node with health === "critical")
    const criticalIds = new Set(nodes.filter(n => n.health === "critical").map(n => n.id));
    const endpointId = (e: LinkEndpoint) => (typeof e === "string" ? e : e.id);
    const criticalLinks = linkData.filter(l =>
      criticalIds.has(endpointId(l.source)) || criticalIds.has(endpointId(l.target))
    );

    const pulseLinks = svg.append("g").selectAll("line")
      .data(criticalLinks).enter().append("line")
      .attr("stroke", "#ff3b5c")
      .attr("stroke-width", 2)
      .attr("stroke-opacity", 0)
      .attr("stroke-dasharray", "6 4");

    function pulseTick() {
      pulseLinks.transition().duration(800).attr("stroke-opacity", 0.7)
        .transition().duration(800).attr("stroke-opacity", 0)
        .on("end", pulseTick);
    }
    pulseTick();

    // Pulse nodes for critical nodes
    const criticalNodeData = nodeData.filter(n => n.health === "critical");
    const pulseNodes = svg.append("g").selectAll("circle")
      .data(criticalNodeData).enter().append("circle")
      .attr("r", 34)
      .attr("fill", "none")
      .attr("stroke", "#ff3b5c")
      .attr("stroke-width", 3)
      .attr("stroke-opacity", 0)
      .attr("filter", "url(#glow-critical)");

    function pulseNodeTick() {
      pulseNodes.transition().duration(800).attr("stroke-opacity", 0.8).attr("r", 34)
        .transition().duration(800).attr("stroke-opacity", 0).attr("r", 48)
        .on("end", pulseNodeTick);
    }
    pulseNodeTick();

    // Node groups
    const nodeG = svg.append("g").selectAll("g")
      .data(nodeData).enter().append("g")
      .attr("cursor", "pointer")
      .on("click", (_event: unknown, d: NodeDatum) => nodeClickRef.current(d))
      .call(
        d3.drag<SVGGElement, NodeDatum>()
          .on("start", (event: DragEventLike, d: NodeDatum) => {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag", (event: DragEventLike, d: NodeDatum) => {
            d.fx = event.x; d.fy = event.y;
          })
          .on("end", (event: DragEventLike, d: NodeDatum) => {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null; d.fy = null;
          })
      );

    // Outer health ring
    nodeG.append("circle")
      .attr("r", 32)
      .attr("fill", "none")
      .attr("stroke", (d: NodeDatum) => HEALTH_COLORS[d.health] || "#555")
      .attr("stroke-width", 2)
      .attr("stroke-opacity", 0.5)
      .attr("filter", (d: NodeDatum) => `url(#glow-${d.health})`);

    // Inner background
    nodeG.append("circle")
      .attr("r", 26)
      .attr("fill", "#0a111c")
      .attr("stroke", (d: NodeDatum) => HEALTH_COLORS[d.health] || "#555")
      .attr("stroke-width", 1.5);

    // Type icon
    nodeG.append("text")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("y", -4)
      .attr("font-size", 14)
      .attr("fill", (d: NodeDatum) => HEALTH_COLORS[d.health])
      .text((d: NodeDatum) => TYPE_ICONS[d.type] || "○");

    // Service label
    nodeG.append("text")
      .attr("text-anchor", "middle")
      .attr("y", 44)
      .attr("font-size", 9)
      .attr("font-family", "'DM Mono', monospace")
      .attr("fill", "#8ba0b8")
      .text((d: NodeDatum) => d.label);

    // Annotation dot (orange badge)
    nodeG.filter((d: NodeDatum) => agentAnnotations.some(a => a.id === d.id))
      .append("circle")
      .attr("r", 5).attr("cx", 20).attr("cy", -20)
      .attr("fill", "#f5a623")
      .attr("stroke", "#0a111c")
      .attr("stroke-width", 1.5);

    sim.on("tick", () => {
      // Save positions for next render cycle
      nodeData.forEach(d => {
        if (d.x != null && d.y != null) positionsRef.current[d.id] = { x: d.x, y: d.y };
      });

      linkSel
        .attr("x1", (d: GraphLink) => (d.source as NodeDatum).x ?? 0)
        .attr("y1", (d: GraphLink) => (d.source as NodeDatum).y ?? 0)
        .attr("x2", (d: GraphLink) => (d.target as NodeDatum).x ?? 0)
        .attr("y2", (d: GraphLink) => (d.target as NodeDatum).y ?? 0);

      pulseLinks
        .attr("x1", (d: GraphLink) => (d.source as NodeDatum).x ?? 0)
        .attr("y1", (d: GraphLink) => (d.source as NodeDatum).y ?? 0)
        .attr("x2", (d: GraphLink) => (d.target as NodeDatum).x ?? 0)
        .attr("y2", (d: GraphLink) => (d.target as NodeDatum).y ?? 0);

      pulseNodes
        .attr("cx", (d: NodeDatum) => d.x ?? 0)
        .attr("cy", (d: NodeDatum) => d.y ?? 0);

      nodeG.attr("transform", (d: NodeDatum) => `translate(${d.x},${d.y})`);
    });

    return () => { sim.stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, links]);

  // ── CopilotKit Hooks ────────────────────────────────────────────────────────

  useCopilotReadable({
    description: "The list of currently degraded or critical services in the cluster.",
    value: nodes.filter(n => n.health !== "healthy").map(n => ({ service: n.id, health: n.health, p99_latency: n.cpu * 40, avg_latency: n.mem * 14 })),
  });

  useCopilotReadable({
    description: "The currently selected service node in the UI.",
    value: selectedNode ? selectedNode.id : null,
  });

  useCopilotReadable({
    description: "Deep Sentry-like insights data for the selected service (including errors, span waterfall, and logs).",
    value: insightsData,
  });

  useCopilotReadable({
    description: "Current cluster scaling status including replicas, queue depth, and recent scale events.",
    value: clusterStatus ? {
      total_replicas: clusterStatus.total_replicas,
      pending_work: clusterStatus.pending_work_items,
      completed: clusterStatus.completed_analyses,
      recent_events: clusterEvents.slice(-5),
    } : null,
  });

  // ── CopilotKit Actions — let AI control the UI ────────────────────────────

  // @ts-ignore - CopilotKit types mismatch
  useCopilotAction({
    name: "selectService",
    description: "Select and analyze a specific service in the dependency graph. Use this when the user asks about a service.",
    parameters: [{ name: "serviceName", type: "string" as const, description: "Service ID like 'payment-service' or 'api-gateway'" }],
    handler: ({ serviceName }: { serviceName: string }) => {
      const node = nodes.find(n => n.id === serviceName);
      if (node) nodeClickRef.current(node as NodeDatum);
      return `Selected ${serviceName}`;
    },
  });

  // @ts-ignore - CopilotKit types mismatch
  useCopilotAction({
    name: "scaleCluster",
    description: "Scale the agent cluster up or down. Use 'up' to add instances when overloaded, 'down' to remove idle instances.",
    parameters: [{ name: "direction", type: "string" as const, description: "'up' or 'down'" }],
    handler: async ({ direction }: { direction: string }) => {
      await handleManualScale(direction as "up" | "down");
      return `Scaled cluster ${direction}. Now at ${clusterStatus?.total_replicas || '?'} replicas.`;
    },
  });

  // @ts-ignore - CopilotKit types mismatch
  useCopilotAction({
    name: "generateInsights",
    description: "Generate optimization insights for all services or a specific one.",
    parameters: [{ name: "serviceName", type: "string" as const, description: "Optional service name, leave empty for all", required: false }],
    handler: async ({ serviceName }: { serviceName?: string }) => {
      await handleGenerateInsights(serviceName);
      setRightTab("insights");
      return `Generated insights${serviceName ? ` for ${serviceName}` : ' for all services'}`;
    },
  });

  // @ts-ignore - CopilotKit types mismatch
  useCopilotAction({
    name: "simulateTrafficSpike",
    description: "Simulate a traffic spike to test auto-scaling. This floods the work queue and triggers the MAPE-K loop.",
    parameters: [],
    handler: async () => {
      await handleSimulateLoad();
      setRightTab("scaling");
      return "Simulated traffic spike. Check the Scaling tab for results.";
    },
  });

  // @ts-ignore - CopilotKit types mismatch
  useCopilotAction({
    name: "runNetworkValidation",
    description: "Run TestSprite network validation to verify all endpoints are healthy after a scale event.",
    parameters: [],
    handler: async () => {
      await handleRunValidation();
      setRightTab("scaling");
      return "Network validation complete. Check the Scaling tab for results.";
    },
  });

  const selectedNodeData = selectedNode ? nodes.find(n => n.id === selectedNode.id) : null;

  // ─── RENDER ──────────────────────────────────────────────────────────────

  return (
    <CopilotErrorBoundary>
      <CopilotKit runtimeUrl="http://localhost:8000/copilotkit" agent="default">
        <div style={{
          fontFamily: "'DM Mono', 'Courier New', monospace",
          background: "#050b14",
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          color: "#c8daf0",
        }}>
          {/* Header */}
          <header style={{
            padding: "12px 24px",
            borderBottom: "1px solid #0e1e2e",
            display: "flex",
            alignItems: "center",
            gap: 16,
            background: "#070e1a",
          }}>
            <span style={{ fontSize: 14, fontWeight: "bold", background: "linear-gradient(135deg, #7b61ff, #00e5a0)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", letterSpacing: 3 }}>NETFORGE</span>
            <span style={{ fontSize: 8, color: "#4a6a8a", marginLeft: 8, border: "1px solid #1a2a3a", padding: "2px 6px", borderRadius: 2 }}>Claude + MiniMax</span>
            <span style={{ flex: 1 }} />
            {[
              { label: "HEALTHY", count: nodes.filter(n => n.health === "healthy").length, color: "#00e5a0" },
              { label: "DEGRADED", count: nodes.filter(n => n.health === "degraded").length, color: "#f5a623" },
              { label: "CRITICAL", count: nodes.filter(n => n.health === "critical").length, color: "#ff3b5c" },
            ].map(s => (
              <span key={s.label} style={{ fontSize: 10, color: s.color, letterSpacing: 2 }}>
                {s.label} <span style={{ fontSize: 14, fontWeight: "bold" }}>{s.count}</span>
              </span>
            ))}
            {/* API status badge */}
            <span style={{
              fontSize: 9,
              color: apiStatus === "connected" ? "#00e5a0" : apiStatus === "offline" ? "#ff3b5c" : "#f5a623",
              letterSpacing: 2,
              marginLeft: 16,
              border: "1px solid currentColor",
              padding: "2px 6px",
              borderRadius: 2,
            }}>
              {apiStatus === "connected" ? "● LIVE" : apiStatus === "offline" ? "● OFFLINE" : "● CONNECTING"}
            </span>
            <span style={{ fontSize: 10, color: "#3a5a7a", marginLeft: 8 }}>
              {new Date().toLocaleTimeString()}
            </span>
          </header>

          {/* Main */}
          <div style={{ display: "flex", flex: 1, overflow: "hidden", height: "calc(100vh - 45px)" }}>

            {/* LEFT — Dependency Graph */}
            <div style={{
              flex: "0 0 58%",
              borderRight: "1px solid #0e1e2e",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}>
              <div style={{
                padding: "10px 18px",
                fontSize: 10,
                color: "#3a5a7a",
                letterSpacing: 2,
                borderBottom: "1px solid #0e1e2e",
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}>
                <span>CONTAINER DEPENDENCY GRAPH</span>
                <span style={{ flex: 1 }} />
                {HEALTHS.map(h => (
                  <span key={h} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: HEALTH_COLORS[h], display: "inline-block" }} />
                    <span style={{ color: "#4a6a8a", fontSize: 9 }}>{h}</span>
                  </span>
                ))}
              </div>

              <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
                <svg ref={svgRef} style={{ width: "100%", height: "100%", display: "block" }} />

                {/* Selected node detail overlay */}
                {selectedNodeData && (
                  <div style={{
                    position: "absolute",
                    bottom: 16,
                    left: 16,
                    background: "#070e1a",
                    border: `1px solid ${HEALTH_COLORS[selectedNodeData.health]}44`,
                    borderLeft: `3px solid ${HEALTH_COLORS[selectedNodeData.health]}`,
                    padding: "12px 16px",
                    width: 240,
                    borderRadius: 4,
                  }}>
                    <div style={{ fontSize: 11, color: "#c8daf0", marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
                      <span>{selectedNodeData.label}</span>
                      <span style={{ color: HEALTH_COLORS[selectedNodeData.health], fontSize: 9, letterSpacing: 1 }}>
                        {(activeRemediations[selectedNodeData.id] || selectedNodeData.health).toUpperCase()}
                      </span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                      {[
                        { label: "THROUGHPUT", value: `${selectedNodeData.rpm} RPM`, color: "#00e5a0" },
                        { label: "ERROR RATE", value: `${selectedNodeData.error_rate}%`, color: selectedNodeData.error_rate > 5 ? "#ff3b5c" : "#00e5a0" },
                      ].map(m => (
                        <div key={m.label} style={{ background: "#060c18", padding: "8px", borderRadius: 4, border: "1px solid #1a2a3a" }}>
                          <div style={{ fontSize: 8, color: "#4a6a8a", marginBottom: 4 }}>{m.label}</div>
                          <div style={{ fontSize: 13, color: m.color, fontWeight: "bold" }}>{m.value}</div>
                        </div>
                      ))}
                    </div>
                    {[
                      { label: "CPU USAGE", value: selectedNodeData.cpu },
                      { label: "MEM USAGE", value: selectedNodeData.mem },
                    ].map(m => (
                      <div key={m.label} style={{ marginBottom: 6 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "#4a6a8a", marginBottom: 2 }}>
                          <span>{m.label}</span><span>{m.value}%</span>
                        </div>
                        <div style={{ height: 3, background: "#0e1e2e", borderRadius: 2, overflow: "hidden" }}>
                          <div style={{
                            height: "100%",
                            width: `${m.value}%`,
                            background: m.value > 80 ? "#ff3b5c" : m.value > 60 ? "#f5a623" : "#00e5a0",
                            borderRadius: 2,
                            transition: "width 0.5s ease",
                          }} />
                        </div>
                      </div>
                    ))}
                    <div style={{ fontSize: 9, color: "#4a6a8a", marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        REPLICAS: <span style={{ color: "#c8daf0" }}>{selectedNodeData.replicas}</span>
                        &nbsp;·&nbsp;TYPE: <span style={{ color: "#c8daf0" }}>{selectedNodeData.type.toUpperCase()}</span>
                      </div>
                      <button
                        onClick={() => setShowInsights(true)}
                        style={{ background: "#7b61ff", border: "none", color: "white", padding: "4px 8px", fontSize: 9, borderRadius: 2, cursor: "pointer", letterSpacing: 1 }}
                      >INSIGHTS</button>
                    </div>
                    <button
                      onClick={() => { setSelectedNode(null); setShowInsights(false); }}
                      style={{ position: "absolute", top: 8, right: 10, background: "none", border: "none", color: "#3a5a7a", cursor: "pointer", fontSize: 12 }}
                    >×</button>
                  </div>
                )}
              </div>

              {/* Agent Annotations bar */}
              <div style={{
                borderTop: "1px solid #0e1e2e",
                padding: "8px 16px",
                background: "#070e1a",
                maxHeight: 90,
                overflowY: "auto",
              }}>
                <div style={{ fontSize: 9, color: "#3a5a7a", letterSpacing: 2, marginBottom: 6 }}>AGENT ANNOTATIONS</div>
                {agentAnnotations.map((a, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, marginBottom: 4, alignItems: "flex-start" }}>
                    <span style={{ fontSize: 9, color: "#3a5a7a", whiteSpace: "nowrap" }}>{a.ts}</span>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#f5a623", flexShrink: 0, marginTop: 1 }} />
                    <span style={{ fontSize: 10, color: "#8ba0b8" }}>
                      <span style={{ color: "#c8daf0" }}>{a.id}</span> — {a.text}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* RIGHT — Tabbed Panel (Agent / Insights) */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0, background: "#050b14" }}>

              {/* Tab Bar */}
              <div style={{
                display: "flex",
                borderBottom: "1px solid #0e1e2e",
                background: "#070e1a",
              }}>
                {([
                  { key: "agent" as const, label: "AGENT", icon: <ActivitySquare size={11} /> },
                  { key: "insights" as const, label: "INSIGHTS", icon: <Lightbulb size={11} /> },
                  { key: "scaling" as const, label: "SCALING", icon: <BarChart3 size={11} /> },
                ]).map(tab => (
                  <button
                    key={tab.key}
                    onClick={() => setRightTab(tab.key)}
                    style={{
                      flex: 1,
                      padding: "10px 18px",
                      fontSize: 10,
                      letterSpacing: 2,
                      border: "none",
                      borderBottom: rightTab === tab.key ? "2px solid #7b61ff" : "2px solid transparent",
                      background: "transparent",
                      color: rightTab === tab.key ? "#c8daf0" : "#3a5a7a",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 6,
                      fontFamily: "'DM Mono', monospace",
                    }}
                  >
                    {tab.icon} {tab.label}
                    {tab.key === "insights" && allInsights.filter(i => i.status === "open").length > 0 && (
                      <span style={{
                        background: "#ff3b5c",
                        color: "#fff",
                        fontSize: 8,
                        padding: "1px 5px",
                        borderRadius: 8,
                        marginLeft: 4,
                      }}>
                        {allInsights.filter(i => i.status === "open").length}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {/* Agent Tab Content */}
              {rightTab === "agent" && (
                <>
                  {/* Dual-Model Status Bar */}
                  <div style={{
                    padding: "8px 18px",
                    fontSize: 10,
                    color: "#3a5a7a",
                    letterSpacing: 2,
                    borderBottom: "1px solid #0e1e2e",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "#070e1a",
                  }}>
                    <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#00e5a0", display: "inline-block", boxShadow: "0 0 6px #00e5a0" }} />
                        <span style={{ color: "#c8daf0" }}>CLAUDE</span>
                        <span style={{ color: "#4a6a8a", fontSize: 8 }}>ORCHESTRATOR</span>
                      </div>
                      <div style={{ width: 1, height: 16, background: "#1a2a3a" }} />
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ width: 6, height: 6, borderRadius: "50%", background: apiStatus === "connected" ? "#7b61ff" : "#f5a623", display: "inline-block", boxShadow: `0 0 6px ${apiStatus === "connected" ? "#7b61ff" : "#f5a623"}` }} />
                        <span style={{ color: "#c8daf0" }}>MINIMAX M2.5</span>
                        <span style={{ color: "#4a6a8a", fontSize: 8 }}>BACKGROUND</span>
                      </div>
                    </div>
                    <span style={{ color: apiStatus === "connected" ? "#00e5a0" : "#f5a623", display: "flex", alignItems: "center", gap: 6 }}>
                      {apiStatus === "connected" ? <ActivitySquare size={12} /> : <ServerCrash size={12} />}
                      {apiStatus === "connected" ? "ONLINE" : "CONNECTING"}
                    </span>
                  </div>

                  <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
                    {/* Left Column: TestSprite + MiniMax Insights + Remediation */}
                    <div style={{ flex: "0 0 38%", borderRight: "1px solid #0e1e2e", display: "flex", flexDirection: "column" }}>

                      {/* TestSprite Panel — Expanded */}
                      <div style={{ borderBottom: "1px solid #0e1e2e", padding: 14 }}>
                        <div style={{ fontSize: 9, color: "#3a5a7a", letterSpacing: 2, marginBottom: 10, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <ShieldAlert size={12} color="#7b61ff" /> TESTSPRITE VALIDATION
                          </span>
                          <button
                            onClick={handleRunValidation}
                            style={{
                              background: "#7b61ff22", border: "1px solid #7b61ff44", color: "#7b61ff",
                              padding: "2px 8px", fontSize: 8, borderRadius: 2, cursor: "pointer",
                              fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                            }}
                          >
                            <Play size={8} style={{ display: "inline", verticalAlign: "middle" }} /> RUN
                          </button>
                        </div>
                        <div style={{
                          background: validationResult.passed ? "#00e5a011" : "#ff3b5c11",
                          border: `1px solid ${validationResult.passed ? "#00e5a0" : "#ff3b5c"}44`,
                          padding: 10,
                          borderRadius: 4,
                          marginBottom: 8,
                        }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, color: validationResult.passed ? "#00e5a0" : "#ff3b5c", fontSize: 11, marginBottom: 4 }}>
                            {validationResult.passed ? <CheckCircle2 size={14} /> : <ServerCrash size={14} />}
                            {validationResult.passed ? "ALL ENDPOINTS PASSING" : "VALIDATION FAILING"}
                          </div>
                          <div style={{ fontSize: 10, color: "#8ba0b8" }}>
                            {validationResult.service}: {validationResult.msg}
                          </div>
                        </div>
                        {/* Validation endpoint breakdown */}
                        {validationResults.length > 0 && (
                          <div style={{ fontSize: 9 }}>
                            {validationResults.slice(-1).map((v: any) => (
                              <div key={v.validation_id}>
                                <div style={{ display: "grid", gridTemplateColumns: "1fr 40px 50px", gap: 2, marginBottom: 4 }}>
                                  {(v.details || []).map((d: any, j: number) => (
                                    <div key={j} style={{ display: "contents" }}>
                                      <span style={{ color: "#8ba0b8" }}>{d.name || d.endpoint}</span>
                                      <span style={{ textAlign: "center", color: d.passed ? "#00e5a0" : "#ff3b5c" }}>{d.passed ? "✓ OK" : "✗ FAIL"}</span>
                                      <span style={{ textAlign: "right", color: "#4a6a8a" }}>{d.latency_ms}ms</span>
                                    </div>
                                  ))}
                                </div>
                                {v.testsprite_results && (
                                  <div style={{ color: "#7b61ff", fontSize: 8, marginTop: 4 }}>
                                    TestSprite: {v.testsprite_results.tests_passed}/{v.testsprite_results.tests_generated} passed · {v.testsprite_results.coverage_percent}% coverage
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Live Agent Activity Feed */}
                      <div style={{ padding: 14, maxHeight: 260, overflowY: "auto" }}>
                        <div style={{ fontSize: 9, color: "#7b61ff", letterSpacing: 2, marginBottom: 10, display: "flex", alignItems: "center", gap: 6 }}>
                          <Lightbulb size={10} /> LIVE AGENT ACTIVITY
                          {agentActivity.length > 0 && (
                            <span style={{ background: "#7b61ff33", padding: "0 6px", borderRadius: 8, fontSize: 8, color: "#7b61ff" }}>{agentActivity.length}</span>
                          )}
                        </div>
                        {agentActivity.length === 0 && allInsights.length === 0 ? (
                          <div style={{ fontSize: 10, color: "#4a6a8a", fontStyle: "italic" }}>Agent will show live activity here when you chat or trigger analysis...</div>
                        ) : (
                          <>
                            {/* Show stored insights first */}
                            {allInsights.slice(0, 5).map((ins: any, i: number) => (
                              <div key={`ins-${i}`} style={{ borderLeft: `2px solid ${ins.severity === 'critical' ? '#ff3b5c' : ins.severity === 'high' ? '#f5a623' : '#7b61ff'}`, paddingLeft: 8, marginBottom: 8 }}>
                                <div style={{ fontSize: 10, color: "#c8daf0", marginBottom: 2 }}>
                                  <span style={{ background: ins.category === 'optimization' ? '#7b61ff33' : '#00e5a022', padding: "0 4px", borderRadius: 2, fontSize: 7, marginRight: 4, color: ins.category === 'optimization' ? '#7b61ff' : '#00e5a0' }}>
                                    {ins.category === 'optimization' ? 'MINIMAX' : ins.category?.toUpperCase() || 'INSIGHT'}
                                  </span>
                                  <span style={{ background: '#1a2a3a', padding: "0 4px", borderRadius: 2, fontSize: 7, marginRight: 4, color: ins.severity === 'critical' ? '#ff3b5c' : ins.severity === 'high' ? '#f5a623' : '#4a6a8a' }}>
                                    {(ins.severity || 'info').toUpperCase()}
                                  </span>
                                  {ins.title}
                                </div>
                                <div style={{ fontSize: 9, color: "#6a8aa0", lineHeight: 1.3 }}>{(ins.insight || "").slice(0, 120)}{ins.insight?.length > 120 ? '...' : ''}</div>
                              </div>
                            ))}
                            {/* Show live tool calls / activity */}
                            {agentActivity.map((act: any) => (
                              <div key={act.id} style={{ borderLeft: `2px solid ${act.event_type === 'error' ? '#ff3b5c' : act.event_type === 'insight_stored' ? '#00e5a0' : act.event_type === 'tool_call' ? '#3a5a7a' : '#7b61ff'}`, paddingLeft: 8, marginBottom: 6 }}>
                                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 1 }}>
                                  <span style={{ fontSize: 8, color: act.event_type === 'tool_call' ? '#3a5a7a' : '#7b61ff', letterSpacing: 1 }}>
                                    {act.event_type === 'tool_call' ? '⚡ TOOL' : act.event_type === 'insight_stored' ? '💡 INSIGHT' : act.event_type === 'analysis' ? '🔍 ANALYSIS' : act.event_type === 'error' ? '⚠ ERROR' : '📊 EVENT'}
                                  </span>
                                  <span style={{ fontSize: 7, color: "#3a5a7a" }}>{act.ts ? new Date(act.ts * 1000).toLocaleTimeString() : ''}</span>
                                </div>
                                <div style={{ fontSize: 10, color: "#c8daf0" }}>{act.summary}</div>
                                {act.detail && <div style={{ fontSize: 8, color: "#4a6a8a", lineHeight: 1.2, marginTop: 1 }}>{act.detail.slice(0, 100)}{act.detail.length > 100 ? '...' : ''}</div>}
                              </div>
                            ))}
                          </>
                        )}
                      </div>
                    </div>

                    {/* CopilotKit Chat UI — embedded inline */}
                    <div style={{ flex: 1, display: "flex", flexDirection: "column", position: "relative", minHeight: 0 }}>
                      <style dangerouslySetInnerHTML={{
                        __html: `
                     .copilotKitChat { height: 100% !important; border: none !important; border-radius: 0 !important; background: transparent !important; font-family: 'DM Mono', monospace !important; }
                     .copilotKitMessages { padding: 12px !important; font-size: 11px !important; }
                     .copilotKitMessage { font-family: 'DM Mono', monospace !important; font-size: 11px !important; background: #080f1c !important; border: 1px solid #0e1e2e !important; color: #c8daf0 !important; border-radius: 6px !important; padding: 8px 12px !important; margin-bottom: 8px !important; }
                     .copilotKitUserMessage { background: #0a1828 !important; border-color: #1a2a3a !important; color: #8ba0b8 !important; }
                     .copilotKitInput, .copilotKitInput textarea { background: #070e1a !important; border: 1px solid #0e1e2e !important; border-radius: 8px !important; font-family: 'DM Mono', monospace !important; font-size: 11px !important; color: #c8daf0 !important; }
                     .copilotKitInput textarea::placeholder { color: #3a5a7a !important; }
                     .copilotKitHeader { display: none !important; }
                     .copilotKitResponseButton { display: none !important; }
                   `}} />
                      {/* @ts-ignore */}
                      <CopilotChat
                        labels={{ initial: "Agent online — ask about cluster health, service metrics, or anomalies." }}
                        className="copilotKitChat"
                      />
                    </div>
                  </div>
                </>
              )}

              {/* Insights Tab Content */}
              {rightTab === "insights" && (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>

                  {/* Insights Header Bar */}
                  <div style={{
                    padding: "10px 18px",
                    fontSize: 10,
                    color: "#3a5a7a",
                    letterSpacing: 2,
                    borderBottom: "1px solid #0e1e2e",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}>
                    <span>PERSISTENT MEMORY — {allInsights.length} INSIGHTS · {allPatterns.length} PATTERNS</span>
                    <button
                      onClick={() => handleGenerateInsights()}
                      disabled={generatingInsights}
                      style={{
                        background: generatingInsights ? "#1a2a3a" : "#7b61ff",
                        border: "none",
                        color: "#fff",
                        padding: "4px 12px",
                        fontSize: 9,
                        borderRadius: 2,
                        cursor: generatingInsights ? "wait" : "pointer",
                        letterSpacing: 1,
                        fontFamily: "'DM Mono', monospace",
                      }}
                    >
                      {generatingInsights ? "GENERATING..." : "GENERATE INSIGHTS"}
                    </button>
                  </div>

                  <div style={{ flex: 1, display: "flex", overflow: "hidden", minHeight: 0 }}>

                    {/* Left: Insights List */}
                    <div style={{ flex: "0 0 55%", borderRight: "1px solid #0e1e2e", overflowY: "auto", minHeight: 0 }}>

                      {/* Recommendations Section */}
                      {allRecommendations.length > 0 && (
                        <div style={{ borderBottom: "1px solid #0e1e2e" }}>
                          <div style={{ padding: "10px 16px", background: "#0a1520", fontSize: 9, color: "#f5a623", letterSpacing: 1, display: "flex", alignItems: "center", gap: 6 }}>
                            <TrendingUp size={10} /> TOP RECOMMENDATIONS
                          </div>
                          {allRecommendations.slice(0, 3).map((rec, i) => (
                            <div key={i} style={{
                              padding: "10px 16px",
                              borderBottom: "1px solid #0e1e2e08",
                              background: "#ff3b5c08",
                            }}>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                                <span style={{ fontSize: 11, color: "#c8daf0" }}>{rec.title}</span>
                                <span style={{
                                  fontSize: 8,
                                  padding: "1px 6px",
                                  borderRadius: 2,
                                  background: rec.severity === "critical" ? "#ff3b5c22" : "#f5a62322",
                                  color: rec.severity === "critical" ? "#ff3b5c" : "#f5a623",
                                  letterSpacing: 1,
                                }}>
                                  {(rec.severity || "").toUpperCase()}
                                </span>
                              </div>
                              <div style={{ fontSize: 10, color: "#8ba0b8", marginBottom: 4 }}>{rec.service}</div>
                              <div style={{ fontSize: 10, color: "#00e5a0" }}>{rec.recommendation}</div>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* All Insights */}
                      <div style={{ padding: "10px 16px", background: "#060c18", fontSize: 9, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #0e1e2e" }}>
                        RECENT INSIGHTS
                      </div>
                      {insightsLoading && allInsights.length === 0 ? (
                        <div style={{ padding: 20, fontSize: 10, color: "#4a6a8a", textAlign: "center" }}>Loading insights...</div>
                      ) : allInsights.length === 0 ? (
                        <div style={{ padding: 20, fontSize: 10, color: "#4a6a8a", textAlign: "center" }}>
                          No insights yet. Click "Generate Insights" to start.
                        </div>
                      ) : (
                        allInsights.map((ins, i) => {
                          const sevColor = ins.severity === "critical" ? "#ff3b5c" :
                            ins.severity === "high" ? "#f5a623" :
                              ins.severity === "medium" ? "#7b61ff" : "#3a5a7a";
                          return (
                            <div key={ins.id || i} style={{
                              padding: "10px 16px",
                              borderBottom: "1px solid #0e1e2e",
                              borderLeft: `3px solid ${sevColor}`,
                              opacity: ins.status === "resolved" ? 0.5 : 1,
                            }}>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                                <span style={{ fontSize: 11, color: "#c8daf0" }}>{ins.title}</span>
                                <span style={{
                                  fontSize: 8,
                                  padding: "1px 6px",
                                  borderRadius: 2,
                                  background: `${sevColor}22`,
                                  color: sevColor,
                                  letterSpacing: 1,
                                }}>
                                  {(ins.severity || "").toUpperCase()}
                                </span>
                              </div>
                              <div style={{ fontSize: 10, color: "#8ba0b8", marginBottom: 4 }}>
                                {ins.service} · {ins.category} · <span style={{ color: ins.status === "open" ? "#f5a623" : ins.status === "acknowledged" ? "#7b61ff" : "#00e5a0" }}>{ins.status}</span>
                              </div>
                              <div style={{ fontSize: 10, color: "#6a8aa0", marginBottom: 6, lineHeight: 1.4 }}>{ins.insight}</div>
                              {ins.status === "open" && (
                                <div style={{ display: "flex", gap: 8 }}>
                                  <button
                                    onClick={() => handleAcknowledgeInsight(ins.id)}
                                    style={{ background: "none", border: "1px solid #7b61ff44", color: "#7b61ff", padding: "2px 8px", fontSize: 8, borderRadius: 2, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, fontFamily: "'DM Mono', monospace" }}
                                  >
                                    <Eye size={8} /> ACK
                                  </button>
                                  <button
                                    onClick={() => handleResolveInsight(ins.id)}
                                    style={{ background: "none", border: "1px solid #00e5a044", color: "#00e5a0", padding: "2px 8px", fontSize: 8, borderRadius: 2, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, fontFamily: "'DM Mono', monospace" }}
                                  >
                                    <CheckCheck size={8} /> RESOLVE
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>

                    {/* Right: Patterns */}
                    <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
                      <div style={{ padding: "10px 16px", background: "#060c18", fontSize: 9, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #0e1e2e" }}>
                        DETECTED PATTERNS
                      </div>
                      {allPatterns.length === 0 ? (
                        <div style={{ padding: 20, fontSize: 10, color: "#4a6a8a", textAlign: "center" }}>
                          No patterns detected yet.
                        </div>
                      ) : (
                        allPatterns.map((pat, i) => (
                          <div key={pat.id || i} style={{
                            padding: "10px 16px",
                            borderBottom: "1px solid #0e1e2e",
                          }}>
                            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                              <span style={{
                                fontSize: 8,
                                padding: "1px 6px",
                                borderRadius: 2,
                                background: "#7b61ff22",
                                color: "#7b61ff",
                                letterSpacing: 1,
                              }}>
                                {(pat.type || "pattern").toUpperCase().replace(/_/g, " ")}
                              </span>
                              <span style={{ fontSize: 9, color: "#4a6a8a" }}>
                                {pat.service || pat.scope || ""}
                              </span>
                            </div>
                            <div style={{ fontSize: 10, color: "#c8daf0", marginBottom: 4, lineHeight: 1.4 }}>{pat.description}</div>
                            <div style={{ display: "flex", gap: 12, fontSize: 9, color: "#4a6a8a" }}>
                              <span>Confidence: <span style={{ color: pat.confidence > 0.8 ? "#00e5a0" : "#f5a623" }}>{((pat.confidence || 0) * 100).toFixed(0)}%</span></span>
                              {pat.occurrences && <span>Seen: <span style={{ color: "#8ba0b8" }}>{pat.occurrences}x</span></span>}
                            </div>
                            {pat.recommendation && (
                              <div style={{ fontSize: 10, color: "#00e5a0", marginTop: 4 }}>{pat.recommendation}</div>
                            )}
                          </div>
                        ))
                      )}

                      {/* Global Patterns section */}
                      {allPatterns.filter(p => p.scope === "global").length > 0 && (
                        <>
                          <div style={{ padding: "10px 16px", background: "#060c18", fontSize: 9, color: "#f5a623", letterSpacing: 1, borderBottom: "1px solid #0e1e2e", display: "flex", alignItems: "center", gap: 6 }}>
                            <AlertTriangle size={10} /> CROSS-SERVICE PATTERNS
                          </div>
                          {allPatterns.filter(p => p.scope === "global").map((gpat, i) => (
                            <div key={gpat.id || i} style={{ padding: "10px 16px", borderBottom: "1px solid #0e1e2e" }}>
                              <div style={{ fontSize: 10, color: "#c8daf0", marginBottom: 4 }}>{gpat.description}</div>
                              {gpat.services_involved && (
                                <div style={{ fontSize: 9, color: "#8ba0b8" }}>Services: {gpat.services_involved.join(", ")}</div>
                              )}
                              {gpat.mitigation && (
                                <div style={{ fontSize: 10, color: "#00e5a0", marginTop: 4 }}>{gpat.mitigation}</div>
                              )}
                            </div>
                          ))}
                        </>
                      )}
                    </div>
                  </div>

                  {/* Cluster Agent Panel — bottom strip */}
                  <div style={{
                    borderTop: "1px solid #0e1e2e",
                    background: "#070e1a",
                    padding: "8px 16px",
                    maxHeight: 200,
                    overflowY: "auto",
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                      <div style={{ fontSize: 9, color: "#3a5a7a", letterSpacing: 2, display: "flex", alignItems: "center", gap: 6 }}>
                        <Network size={10} color="#7b61ff" /> MAPE-K CLUSTER — {clusterStatus?.total_replicas || 1} AGENT{(clusterStatus?.total_replicas || 1) > 1 ? "S" : ""}
                        <span style={{ color: "#4a6a8a" }}>·</span>
                        <span style={{ color: "#8ba0b8" }}>{clusterStatus?.pending_work_items || 0} queued</span>
                        <span style={{ color: "#4a6a8a" }}>·</span>
                        <span style={{ color: "#8ba0b8" }}>{clusterStatus?.completed_analyses || 0} done</span>
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          onClick={handleMapeKTick}
                          style={{
                            background: "none", border: "1px solid #7b61ff44", color: "#7b61ff",
                            padding: "2px 8px", fontSize: 8, borderRadius: 2, cursor: "pointer",
                            fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          }}
                        >
                          TICK
                        </button>
                        <button
                          onClick={handleSimulateLoad}
                          disabled={simulatingLoad}
                          style={{
                            background: simulatingLoad ? "#1a2a3a" : "#ff3b5c22",
                            border: "1px solid #ff3b5c44", color: "#ff3b5c",
                            padding: "2px 8px", fontSize: 8, borderRadius: 2,
                            cursor: simulatingLoad ? "wait" : "pointer",
                            fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          }}
                        >
                          {simulatingLoad ? "SIMULATING..." : "SIMULATE LOAD"}
                        </button>
                      </div>
                    </div>

                    {/* Agent replica tiles */}
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {(clusterStatus?.replicas || []).map((replica: any) => (
                        <div key={replica.replica_id} style={{
                          background: "#0a111c",
                          border: `1px solid ${replica.status === "running" ? "#00e5a044" : "#ff3b5c44"}`,
                          borderRadius: 4,
                          padding: "6px 10px",
                          minWidth: 140,
                          flex: "0 0 auto",
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                            <span style={{ fontSize: 10, color: "#c8daf0", display: "flex", alignItems: "center", gap: 4 }}>
                              <Cpu size={9} color="#7b61ff" /> {replica.name}
                            </span>
                            <span style={{
                              width: 6, height: 6, borderRadius: "50%",
                              background: replica.status === "running" ? "#00e5a0" : "#ff3b5c",
                              display: "inline-block",
                            }} />
                          </div>
                          <div style={{ fontSize: 9, color: "#4a6a8a", marginBottom: 2 }}>
                            svcs: <span style={{ color: "#8ba0b8" }}>{replica.assigned_services?.length || 0}</span>
                            {" "}· done: <span style={{ color: "#8ba0b8" }}>{replica.analyses_completed}</span>
                          </div>
                          {/* CPU bar */}
                          <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                            <span style={{ fontSize: 8, color: "#4a6a8a", width: 22 }}>CPU</span>
                            <div style={{ flex: 1, height: 3, background: "#0e1e2e", borderRadius: 2, overflow: "hidden" }}>
                              <div style={{
                                height: "100%",
                                width: `${replica.cpu_load || 0}%`,
                                background: (replica.cpu_load || 0) > 80 ? "#ff3b5c" : (replica.cpu_load || 0) > 50 ? "#f5a623" : "#00e5a0",
                                borderRadius: 2,
                              }} />
                            </div>
                            <span style={{ fontSize: 8, color: "#8ba0b8", width: 28, textAlign: "right" }}>{(replica.cpu_load || 0).toFixed(0)}%</span>
                          </div>
                          {replica.current_task && (
                            <div style={{ fontSize: 8, color: "#f5a623", display: "flex", alignItems: "center", gap: 4 }}>
                              <Zap size={8} /> {replica.current_task}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>

                    {/* Recent scale events */}
                    {clusterEvents.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        {clusterEvents.slice(-3).reverse().map((evt, i) => (
                          <div key={i} style={{ fontSize: 9, color: "#4a6a8a", display: "flex", gap: 6, marginBottom: 2 }}>
                            <span style={{ color: evt.event === "spawn" ? "#00e5a0" : "#ff3b5c" }}>
                              {evt.event === "spawn" ? "+" : "-"}
                            </span>
                            <span style={{ color: "#8ba0b8" }}>{evt.name}</span>
                            <span>{evt.reason}</span>
                            <span style={{ color: "#3a5a7a" }}>({evt.total_replicas} total)</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Scaling Tab Content */}
              {rightTab === "scaling" && (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

                  {/* Scaling Controls Bar */}
                  <div style={{
                    padding: "12px 18px",
                    borderBottom: "1px solid #0e1e2e",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    background: "#070e1a",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 10, color: "#3a5a7a", letterSpacing: 2 }}>
                      <Network size={12} color="#7b61ff" />
                      INSTANCES: <span style={{ fontSize: 18, fontWeight: "bold", color: "#c8daf0" }}>{clusterStatus?.total_replicas || 1}</span>
                      <span style={{ color: "#4a6a8a" }}>·</span>
                      <span style={{ color: "#8ba0b8" }}>{clusterStatus?.pending_work_items || 0} queued</span>
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => handleManualScale("up")}
                        disabled={scalingInProgress}
                        style={{
                          background: "#00e5a015", border: "1px solid #00e5a044", color: "#00e5a0",
                          padding: "6px 14px", fontSize: 10, borderRadius: 4, cursor: "pointer",
                          fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          display: "flex", alignItems: "center", gap: 6,
                          opacity: scalingInProgress ? 0.5 : 1,
                        }}
                      >
                        <ArrowUpCircle size={14} /> SCALE UP
                      </button>
                      <button
                        onClick={() => handleManualScale("down")}
                        disabled={scalingInProgress || (clusterStatus?.total_replicas || 1) <= 1}
                        style={{
                          background: "#ff3b5c15", border: "1px solid #ff3b5c44", color: "#ff3b5c",
                          padding: "6px 14px", fontSize: 10, borderRadius: 4, cursor: "pointer",
                          fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          display: "flex", alignItems: "center", gap: 6,
                          opacity: scalingInProgress || (clusterStatus?.total_replicas || 1) <= 1 ? 0.5 : 1,
                        }}
                      >
                        <ArrowDownCircle size={14} /> SCALE DOWN
                      </button>
                      <button
                        onClick={handleSimulateLoad}
                        disabled={simulatingLoad}
                        style={{
                          background: "#f5a62315", border: "1px solid #f5a62344", color: "#f5a623",
                          padding: "6px 14px", fontSize: 10, borderRadius: 4, cursor: "pointer",
                          fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          display: "flex", alignItems: "center", gap: 6,
                        }}
                      >
                        <Zap size={14} /> {simulatingLoad ? "SIMULATING..." : "SIMULATE LOAD"}
                      </button>
                      <button
                        onClick={handleRunValidation}
                        style={{
                          background: "#7b61ff15", border: "1px solid #7b61ff44", color: "#7b61ff",
                          padding: "6px 14px", fontSize: 10, borderRadius: 4, cursor: "pointer",
                          fontFamily: "'DM Mono', monospace", letterSpacing: 1,
                          display: "flex", alignItems: "center", gap: 6,
                        }}
                      >
                        <ShieldAlert size={14} /> VALIDATE
                      </button>
                    </div>
                  </div>

                  <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

                    {/* Left: Instance Cards + Timeline */}
                    <div style={{ flex: "0 0 50%", borderRight: "1px solid #0e1e2e", overflowY: "auto" }}>

                      {/* Instance Cards */}
                      <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 9, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #0e1e2e" }}>
                        RUNNING INSTANCES
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: 12 }}>
                        {(clusterStatus?.replicas || []).map((replica: any) => (
                          <div key={replica.replica_id} style={{
                            background: "#0a111c",
                            border: `1px solid ${replica.status === "running" ? "#00e5a033" : "#ff3b5c33"}`,
                            borderRadius: 6,
                            padding: "10px 14px",
                            minWidth: 180,
                            flex: "1 1 180px",
                            backdropFilter: "blur(4px)",
                          }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                              <span style={{ fontSize: 11, color: "#c8daf0", display: "flex", alignItems: "center", gap: 6 }}>
                                <Cpu size={12} color="#7b61ff" /> {replica.name}
                              </span>
                              <span style={{
                                width: 8, height: 8, borderRadius: "50%",
                                background: replica.status === "running" ? "#00e5a0" : "#ff3b5c",
                                display: "inline-block",
                                boxShadow: `0 0 6px ${replica.status === "running" ? "#00e5a0" : "#ff3b5c"}`,
                              }} />
                            </div>
                            <div style={{ fontSize: 9, color: "#4a6a8a", marginBottom: 4 }}>
                              Services: <span style={{ color: "#8ba0b8" }}>{replica.assigned_services?.length || 0}</span>
                              {" · "}Done: <span style={{ color: "#8ba0b8" }}>{replica.analyses_completed}</span>
                            </div>
                            {/* CPU bar */}
                            <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 4 }}>
                              <span style={{ fontSize: 8, color: "#4a6a8a", width: 24 }}>CPU</span>
                              <div style={{ flex: 1, height: 4, background: "#0e1e2e", borderRadius: 2, overflow: "hidden" }}>
                                <div style={{
                                  height: "100%",
                                  width: `${replica.cpu_load || 0}%`,
                                  background: (replica.cpu_load || 0) > 80 ? "#ff3b5c" : (replica.cpu_load || 0) > 50 ? "#f5a623" : "#00e5a0",
                                  borderRadius: 2,
                                  transition: "width 0.5s ease",
                                }} />
                              </div>
                              <span style={{ fontSize: 8, color: "#8ba0b8", width: 28, textAlign: "right" }}>{(replica.cpu_load || 0).toFixed(0)}%</span>
                            </div>
                            {replica.current_task && (
                              <div style={{ fontSize: 8, color: "#f5a623", display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
                                <Zap size={8} /> {replica.current_task}
                              </div>
                            )}
                            {replica.assigned_services && replica.assigned_services.length > 0 && (
                              <div style={{ fontSize: 8, color: "#3a5a7a", marginTop: 4, lineHeight: 1.6 }}>
                                {replica.assigned_services.map((s: string) => (
                                  <span key={s} style={{ background: "#0e1e2e", padding: "1px 5px", borderRadius: 2, marginRight: 4, display: "inline-block", marginBottom: 2 }}>{s}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>

                      {/* Scale Event Timeline */}
                      <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 9, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #0e1e2e", borderTop: "1px solid #0e1e2e" }}>
                        SCALE EVENT TIMELINE
                      </div>
                      <div style={{ padding: 12 }}>
                        {(scaleReport?.instance_timeline || clusterEvents || []).length === 0 ? (
                          <div style={{ fontSize: 10, color: "#4a6a8a", textAlign: "center", padding: 20 }}>No scale events yet. Click "Simulate Load" to trigger auto-scaling.</div>
                        ) : (
                          (scaleReport?.instance_timeline || clusterEvents || []).map((evt: any, i: number) => (
                            <div key={i} style={{
                              display: "flex",
                              alignItems: "flex-start",
                              gap: 10,
                              marginBottom: 12,
                              paddingLeft: 10,
                              borderLeft: `2px solid ${evt.event === "spawn" ? "#00e5a0" : "#ff3b5c"}`,
                            }}>
                              <div style={{
                                width: 24, height: 24, borderRadius: "50%",
                                background: evt.event === "spawn" ? "#00e5a015" : "#ff3b5c15",
                                border: `1px solid ${evt.event === "spawn" ? "#00e5a044" : "#ff3b5c44"}`,
                                display: "flex", alignItems: "center", justifyContent: "center",
                                flexShrink: 0,
                              }}>
                                {evt.event === "spawn" ? <ArrowUpCircle size={12} color="#00e5a0" /> : <ArrowDownCircle size={12} color="#ff3b5c" />}
                              </div>
                              <div>
                                <div style={{ fontSize: 11, color: "#c8daf0", marginBottom: 2 }}>
                                  {evt.event === "spawn" ? "Instance Added" : "Instance Removed"}: <span style={{ color: "#7b61ff" }}>{evt.name}</span>
                                </div>
                                <div style={{ fontSize: 9, color: "#4a6a8a" }}>
                                  {evt.reason} · <span style={{ color: "#8ba0b8" }}>{evt.total_after || evt.total_replicas} total</span>
                                </div>
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Right: Validation Results + Report Summary */}
                    <div style={{ flex: 1, overflowY: "auto" }}>

                      {/* TestSprite Validation Results */}
                      <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 9, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #0e1e2e", display: "flex", alignItems: "center", gap: 6 }}>
                        <ShieldAlert size={10} color="#7b61ff" /> TESTSPRITE VALIDATIONS
                      </div>
                      {validationResults.length === 0 ? (
                        <div style={{ fontSize: 10, color: "#4a6a8a", textAlign: "center", padding: 20 }}>No validations yet. Click "Validate" to run.</div>
                      ) : (
                        validationResults.slice().reverse().map((v: any, i: number) => (
                          <div key={v.validation_id || i} style={{
                            padding: "10px 16px",
                            borderBottom: "1px solid #0e1e2e",
                            borderLeft: `3px solid ${v.status === "passed" ? "#00e5a0" : v.status === "partial" ? "#f5a623" : "#ff3b5c"}`,
                          }}>
                            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                              <span style={{ fontSize: 11, color: v.status === "passed" ? "#00e5a0" : "#ff3b5c", display: "flex", alignItems: "center", gap: 6 }}>
                                {v.status === "passed" ? <CheckCircle2 size={12} /> : <ServerCrash size={12} />}
                                {v.status?.toUpperCase()}
                              </span>
                              <span style={{ fontSize: 9, color: "#4a6a8a" }}>{v.trigger_event} · {v.total_duration_ms}ms</span>
                            </div>
                            {/* Endpoint results table */}
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 60px 60px", gap: 2, fontSize: 9 }}>
                              <span style={{ color: "#3a5a7a" }}>ENDPOINT</span>
                              <span style={{ color: "#3a5a7a", textAlign: "center" }}>STATUS</span>
                              <span style={{ color: "#3a5a7a", textAlign: "right" }}>LATENCY</span>
                              {(v.details || []).map((d: any, j: number) => (
                                <>
                                  <span key={`e${j}`} style={{ color: "#8ba0b8" }}>{d.name || d.endpoint}</span>
                                  <span key={`s${j}`} style={{ textAlign: "center", color: d.passed ? "#00e5a0" : "#ff3b5c" }}>{d.passed ? "✓" : "✗"}</span>
                                  <span key={`l${j}`} style={{ textAlign: "right", color: "#8ba0b8" }}>{d.latency_ms}ms</span>
                                </>
                              ))}
                            </div>
                            {/* TestSprite summary */}
                            {v.testsprite_results && (
                              <div style={{ marginTop: 8, padding: 8, background: "#0a111c", borderRadius: 4, border: "1px solid #0e1e2e" }}>
                                <div style={{ fontSize: 9, color: "#7b61ff", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                                  <ShieldAlert size={8} /> TestSprite: {v.testsprite_results.tests_passed}/{v.testsprite_results.tests_generated} passed · {v.testsprite_results.coverage_percent}% coverage
                                </div>
                              </div>
                            )}
                          </div>
                        ))
                      )}

                      {/* Scale Report Summary */}
                      {scaleReport && (
                        <>
                          <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 9, color: "#f5a623", letterSpacing: 1, borderBottom: "1px solid #0e1e2e", borderTop: "1px solid #0e1e2e", display: "flex", alignItems: "center", gap: 6 }}>
                            <FileText size={10} /> SCALE REPORT SUMMARY
                          </div>
                          <div style={{ padding: 16 }}>
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8, marginBottom: 16 }}>
                              {[
                                { label: "SCALE UPS", value: scaleReport.scaling_summary?.total_scale_ups || 0, color: "#00e5a0" },
                                { label: "SCALE DOWNS", value: scaleReport.scaling_summary?.total_scale_downs || 0, color: "#ff3b5c" },
                                { label: "MAX INSTANCES", value: scaleReport.scaling_summary?.max_instances_reached || 1, color: "#7b61ff" },
                                { label: "VALIDATIONS", value: `${scaleReport.validations?.passed || 0}/${scaleReport.validations?.total || 0}`, color: "#f5a623" },
                              ].map(m => (
                                <div key={m.label} style={{ background: "#0a111c", padding: 10, borderRadius: 4, border: "1px solid #0e1e2e", textAlign: "center" }}>
                                  <div style={{ fontSize: 8, color: "#4a6a8a", marginBottom: 4 }}>{m.label}</div>
                                  <div style={{ fontSize: 16, color: m.color, fontWeight: "bold" }}>{m.value}</div>
                                </div>
                              ))}
                            </div>
                            {/* Actions taken */}
                            {(scaleReport.actions || []).length > 0 && (
                              <>
                                <div style={{ fontSize: 9, color: "#4a6a8a", letterSpacing: 1, marginBottom: 8 }}>REMEDIATION ACTIONS</div>
                                {scaleReport.actions.slice(0, 5).map((a: any, i: number) => (
                                  <div key={i} style={{ borderLeft: "2px solid #7b61ff", paddingLeft: 10, marginBottom: 8 }}>
                                    <div style={{ fontSize: 10, color: "#c8daf0" }}>{a.action_type?.toUpperCase()} on {a.service}</div>
                                    <div style={{ fontSize: 9, color: "#00e5a0" }}>{a.result?.message || a.result?.result || JSON.stringify(a.result).slice(0, 80)}</div>
                                  </div>
                                ))}
                              </>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Insights Modal Overlay */}
          {showInsights && insightsData && (
            <div style={{
              position: "absolute", top: 45, left: 0, right: 0, bottom: 0,
              background: "rgba(5, 11, 20, 0.95)",
              backdropFilter: "blur(8px)",
              zIndex: 100,
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              padding: 40
            }}>
              <div style={{
                width: "100%", maxWidth: 1000, height: "100%",
                background: "#0a111c", border: "1px solid #1a2a3a",
                borderRadius: 8, display: "flex", flexDirection: "column",
                boxShadow: "0 20px 40px rgba(0,0,0,0.5)", overflow: "hidden"
              }}>
                {/* Header */}
                <div style={{ padding: "16px 24px", borderBottom: "1px solid #1a2a3a", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ background: "#7b61ff22", color: "#7b61ff", padding: "4px 8px", borderRadius: 4, fontSize: 11, fontWeight: "bold" }}>DEEP INSIGHTS</div>
                    <div style={{ fontSize: 16, color: "#c8daf0" }}>{insightsData.service}</div>
                  </div>
                  <button
                    onClick={() => setShowInsights(false)}
                    style={{ background: "none", border: "none", color: "#8ba0b8", cursor: "pointer", fontSize: 20 }}
                  >×</button>
                </div>

                {/* Body */}
                <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

                  {/* Left: Issues List */}
                  <div style={{ flex: "0 0 40%", borderRight: "1px solid #1a2a3a", display: "flex", flexDirection: "column" }}>
                    <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 10, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #1a2a3a" }}>UNRESOLVED ISSUES</div>
                    <div style={{ flex: 1, overflowY: "auto" }}>
                      {insightsData.errors && insightsData.errors.map((err: any) => (
                        <div key={err.id} style={{ padding: 16, borderBottom: "1px solid #1a2a3a", cursor: "pointer", background: err.trend === 'up' ? "#ff3b5c05" : "transparent" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                            <span style={{ color: "#ff3b5c", fontSize: 12, fontWeight: "bold" }}>{err.type}</span>
                            <span style={{ fontSize: 10, color: "#8ba0b8" }}>{err.id}</span>
                          </div>
                          <div style={{ fontSize: 11, color: "#c8daf0", marginBottom: 12, lineHeight: 1.4 }}>{err.message}</div>
                          <div style={{ display: "flex", gap: 16, fontSize: 10, color: "#4a6a8a" }}>
                            <span><span style={{ color: "#8ba0b8" }}>{err.count}</span> events</span>
                            <span><span style={{ color: "#8ba0b8" }}>{err.unique_users}</span> users</span>
                            <span style={{ color: err.trend === 'up' ? "#ff3b5c" : "#00e5a0" }}>{err.trend === 'up' ? '↗ TRENDING' : '↘ DROPPING'}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Right: Waterfall & Details */}
                  <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
                    <div style={{ padding: "12px 16px", background: "#060c18", fontSize: 10, color: "#4a6a8a", letterSpacing: 1, borderBottom: "1px solid #1a2a3a" }}>
                      TRACE WATERFALL: <span style={{ color: "#c8daf0" }}>{insightsData.waterfall?.trace_id}</span>
                    </div>

                    <div style={{ padding: 20, flex: 1, overflowY: "auto" }}>
                      {/* Waterfall */}
                      <div style={{ marginBottom: 30 }}>
                        <div style={{ fontSize: 11, color: "#8ba0b8", marginBottom: 12 }}>TOTAL DURATION: {insightsData.waterfall?.total_duration_ms}ms</div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                          {insightsData.waterfall?.spans.map((span: any) => (
                            <div key={span.span_id} style={{ position: "relative", height: 28, background: "#050b14", borderRadius: 4 }}>
                              <div style={{
                                position: "absolute", left: `${(span.start_offset_ms / insightsData.waterfall.total_duration_ms) * 100}%`,
                                width: `${Math.max(2, (span.duration_ms / insightsData.waterfall.total_duration_ms) * 100)}%`,
                                height: "100%", background: span.type === "db" ? "#f5a623" : span.type === "cache" ? "#7b61ff" : "#00e5a0",
                                borderRadius: 4, opacity: 0.8
                              }} />
                              <div style={{ position: "absolute", left: 8, top: 6, fontSize: 10, color: "#fff", textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}>
                                {span.operation} <span style={{ opacity: 0.7 }}>— {span.duration_ms}ms</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Meta Cards */}
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                        {/* Release Correlation */}
                        <div style={{ background: "#050b14", border: "1px solid #1a2a3a", borderRadius: 6, padding: 16 }}>
                          <div style={{ fontSize: 10, color: "#4a6a8a", letterSpacing: 1, marginBottom: 12 }}>RELEASE CORRELATION</div>
                          <div style={{ fontSize: 14, color: "#c8daf0", marginBottom: 8 }}>{insightsData.releases?.current_version}</div>
                          <div style={{ fontSize: 11, color: "#8ba0b8", marginBottom: 4 }}>{insightsData.releases?.new_issues_introduced} new issues introduced</div>
                          <div style={{ fontSize: 11, color: insightsData.releases?.status === 'degraded' ? '#ff3b5c' : '#00e5a0' }}>Crash rate delta: {insightsData.releases?.crash_rate_delta}</div>
                        </div>

                        {/* Correlated Logs */}
                        <div style={{ background: "#050b14", border: "1px solid #1a2a3a", borderRadius: 6, padding: 16 }}>
                          <div style={{ fontSize: 10, color: "#4a6a8a", letterSpacing: 1, marginBottom: 12 }}>CORRELATED LOGS</div>
                          {insightsData.logs?.slice(-3).map((l: any, i: number) => (
                            <div key={i} style={{ fontSize: 10, marginBottom: 6, display: "flex", gap: 8 }}>
                              <span style={{ color: l.level === 'ERROR' ? '#ff3b5c' : l.level === 'WARN' ? '#f5a623' : '#3a5a7a' }}>[{l.level}]</span>
                              <span style={{ color: "#c8daf0" }}>{l.message}</span>
                            </div>
                          ))}
                        </div>
                      </div>

                    </div>
                  </div>

                </div>
              </div>
            </div>
          )}

          <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #050b14; }
        ::-webkit-scrollbar-thumb { background: #0e1e2e; border-radius: 2px; }
        @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0; } }
      `}</style>
        </div>
      </CopilotKit>
    </CopilotErrorBoundary>
  );
}
