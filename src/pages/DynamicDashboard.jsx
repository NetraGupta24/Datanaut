import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import GlassCard from "../ui/GlassCard.jsx";
import ChartWithRealTimeData from "../ui/ChartWithRealTimeData.jsx";
import {
  classifyDynamicCharts,
  normalizeDynamicCharts,
  saveDynamicDashboardCache,
  loadDynamicDashboardCache
} from "../utils/dynamicChartUtils.js";

const BACKEND_URL = "http://localhost:8085"; // change if backend runs elsewhere
const WS_URL = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss://localhost:8085" : "ws://localhost:8085";
const MAX_ALERTS = 50;

const formatNumber = (value) => {
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  return value.toString();
};

const formatReading = (value) =>
  typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(value ?? "");

const formatTime = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString();
};

// One socket for the whole page that collects the anomalies the backend flags on any connection
function useAnomalyAlerts() {
  const [alerts, setAlerts] = useState([]);

  useEffect(() => {
    let ws = null;
    let retryTimer = null;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(WS_URL);
      const scheduleReconnect = () => {
        if (stopped || retryTimer) return;
        retryTimer = setTimeout(() => {
          retryTimer = null;
          connect();
        }, 2000);
      };
      ws.onerror = scheduleReconnect;
      ws.onclose = scheduleReconnect;
      ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg?.type !== "update" || !msg.anomaly || !Array.isArray(msg.anomalies)) return;
        const row = Array.isArray(msg.rows) ? msg.rows[0] : null;
        const at = row?.timestamp || new Date().toISOString();
        const device = row?.device_id ?? row?.deviceId ?? null;
        const incoming = msg.anomalies.map((a) => ({
          ...a,
          key: `${msg.id}|${msg.topic}|${at}|${a.field}`,
          connectionId: msg.id,
          device,
          at
        }));
        setAlerts((prev) => [...incoming, ...prev].slice(0, MAX_ALERTS));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      try {
        if (ws) ws.close();
      } catch {
        // ignore
      }
    };
  }, []);

  return [alerts, () => setAlerts([])];
}

function AnomalyAlerts({ alerts, connections, onClear }) {
  const nameOf = (id) => connections.find((c) => c.id === id)?.config?.name || id;
  return (
    <GlassCard className="shadow-xl">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Anomaly alerts</h2>
          <p className="text-xs text-slate-500 dark:text-slate-300">
            Live readings far from their recent values (rolling z-score; |z| above 3 by default).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              alerts.length
                ? "bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-200"
                : "bg-slate-100 text-slate-500 dark:bg-slate-800/60 dark:text-slate-300"
            }`}
          >
            {alerts.length} {alerts.length === 1 ? "alert" : "alerts"}
          </span>
          {alerts.length ? (
            <button
              onClick={onClear}
              className="glass-hover rounded-full border border-white/20 px-2.5 py-0.5 text-[11px] font-semibold text-slate-600 transition hover:bg-white/10 dark:border-slate-200/20 dark:text-slate-300 dark:hover:bg-white/5"
            >
              Clear
            </button>
          ) : null}
        </div>
      </div>
      {alerts.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">No anomalies yet. Alerts appear here as live readings arrive.</p>
      ) : (
        <ul className="mt-3 max-h-64 space-y-2 overflow-auto pr-1" aria-live="polite">
          {alerts.map((a) => (
            <li
              key={a.key}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-red-200 bg-red-50/80 px-3 py-2 text-sm text-red-800 dark:border-red-400/30 dark:bg-red-500/10 dark:text-red-200"
            >
              <div className="min-w-0">
                <span className="font-semibold">
                  {a.field} = {formatReading(a.value)}
                </span>
                <span className="ml-2 text-xs">
                  usual {formatReading(a.mean)} ± {formatReading(a.std)}
                </span>
                <div className="truncate text-xs text-red-700/80 dark:text-red-300/80">
                  {nameOf(a.connectionId)}
                  {a.device ? ` · ${a.device}` : ""} · {formatTime(a.at)}
                </div>
              </div>
              <span className="rounded-full bg-red-600 px-2 py-0.5 text-xs font-semibold tabular-nums text-white">
                z = {a.score > 0 ? "+" : ""}
                {a.score}
              </span>
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

// Memoized chart card component to prevent unnecessary re-renders
const ChartCard = React.memo(({ chart, connections, onEdit, onDuplicate, onDelete }) => {
  const connection = connections.find(c => c.id === chart.dataSource);
  const connectionName = connection?.config?.name || chart.dataSource || "Unknown connection";
  
  return (
    <GlassCard className="flex flex-col gap-3 shadow-xl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">{chart.title}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-300">
            {connectionName} - Updated {chart.updatedAt ? new Date(chart.updatedAt).toLocaleString() : "unknown"}
          </p>
        </div>
        <div className="flex gap-2 text-xs font-semibold">
          <button
            onClick={() => onEdit(chart.id)}
            className="glass-hover rounded-full border border-white/20 px-3 py-1 text-slate-600 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-100 dark:border-slate-200/20 dark:text-slate-300 dark:hover:bg-white/5 dark:focus-visible:ring-offset-slate-900"
          >
            Edit
          </button>
          <button
            onClick={() => onDuplicate(chart.id)}
            className="glass-hover rounded-full border border-white/20 px-3 py-1 text-slate-600 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-100 dark:border-slate-200/20 dark:text-slate-300 dark:hover:bg-white/5 dark:focus-visible:ring-offset-slate-900"
          >
            Duplicate
          </button>
          <button
            onClick={() => onDelete(chart.id)}
            className="glass-hover rounded-full border border-transparent px-3 py-1 text-red-500 transition hover:border-red-100 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-100 dark:hover:bg-red-500/20 dark:focus-visible:ring-offset-slate-900"
          >
            Delete
          </button>
        </div>
      </div>
      <div className="flex-1 rounded-2xl bg-slate-50 p-4 transition-colors dark:bg-slate-800/50">
        <ChartWithRealTimeData
          chart={chart}
          onEdit={onEdit}
          onDuplicate={onDuplicate}
          onDelete={onDelete}
        />
      </div>
      <div className="grid gap-3 text-xs text-slate-500 sm:grid-cols-3 dark:text-slate-300">
        <div className="rounded-xl bg-slate-100 px-3 py-2 transition-colors dark:bg-slate-800/60">
          <span className="block font-semibold text-slate-700 dark:text-slate-200">Type</span>
          <span className="uppercase">{chart.type || chart.chartType || "line"}</span>
        </div>
        <div className="rounded-xl bg-slate-100 px-3 py-2 transition-colors dark:bg-slate-800/60">
          <span className="block font-semibold text-slate-700 dark:text-slate-200">Dimension</span>
          <span className="uppercase">{chart.dimension || "2d"}</span>
        </div>
        <div className="rounded-xl bg-slate-100 px-3 py-2 transition-colors dark:bg-slate-800/60">
          <span className="block font-semibold text-slate-700 dark:text-slate-200">Source</span>
          <span className="truncate">{connectionName}</span>
        </div>
      </div>
    </GlassCard>
  );
}, (prevProps, nextProps) => {
  // Custom comparison to prevent re-renders when chart data hasn't changed
  return (
    prevProps.chart.id === nextProps.chart.id &&
    prevProps.chart.updatedAt === nextProps.chart.updatedAt &&
    prevProps.connections.length === nextProps.connections.length
  );
});

ChartCard.displayName = 'ChartCard';

export default function DynamicDashboard() {
  const [chartRecords, setChartRecords] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusMessage, setStatusMessage] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const navigate = useNavigate();
  const abortControllerRef = useRef(null);
  const [anomalyAlerts, clearAnomalyAlerts] = useAnomalyAlerts();

  const normalizedCharts = useMemo(() => normalizeDynamicCharts(chartRecords), [chartRecords]);
  const { twoD: charts2D, threeD: charts3D } = useMemo(
    () => classifyDynamicCharts(normalizedCharts),
    [normalizedCharts]
  );

  const metrics = useMemo(() => {
    return [
      {
        label: "Connections",
        value: connections.length,
        trend: "Live data",
        color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200"
      },
      {
        label: "Charts",
        value: normalizedCharts.length,
        trend: "Dynamic visualizations",
        color: "bg-sky-100 text-sky-700 dark:bg-sky-500/20 dark:text-sky-200"
      },
      {
        label: "Last update",
        value: lastUpdated ? new Date(lastUpdated).toLocaleString() : "N/A",
        trend: "Auto-saved",
        color: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200"
      }
    ];
  }, [connections.length, normalizedCharts.length, lastUpdated]);

  const persistCache = useCallback(
    (charts, connectionList, fetchedAt = Date.now()) => {
      const normalized = normalizeDynamicCharts(charts);
      saveDynamicDashboardCache({
        charts: normalized,
        connections: connectionList,
        fetchedAt
      });
    },
    []
  );

  const fetchDashboardData = useCallback(async () => {
    // Cancel any in-flight requests
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    abortControllerRef.current = new AbortController();
    const signal = abortControllerRef.current.signal;
    
    setLoading(true);
    try {
      const [connectionsResponse, chartsResponse] = await Promise.all([
        fetch(`${BACKEND_URL}/api/connections`, { signal }),
        fetch(`${BACKEND_URL}/api/charts`, { signal })
      ]);

      const connectionsData = await connectionsResponse.json();
      const chartsData = await chartsResponse.json();

      const nextConnections = connectionsData?.success ? connectionsData.connections || [] : [];
      const nextCharts = chartsData?.success ? chartsData.charts || [] : [];
      const timestamp = Date.now();

      setConnections(nextConnections);
      setChartRecords(normalizeDynamicCharts(nextCharts));
      setLastUpdated(timestamp);
      setStatusMessage(null);
      persistCache(nextCharts, nextConnections, timestamp);
    } catch (error) {
      // Don't set error if request was aborted
      if (error.name === 'AbortError') {
        return;
      }
      console.error("Error fetching data:", error);
      const cached = loadDynamicDashboardCache();
      if (cached) {
        setConnections(cached.connections || []);
        setChartRecords(normalizeDynamicCharts(cached.charts || []));
        setLastUpdated(cached.fetchedAt || Date.now());
        setStatusMessage("Live data unavailable. Showing cached charts.");
      } else {
        setStatusMessage("Failed to load dashboard data. Please try again later.");
      }
    } finally {
      setLoading(false);
    }
  }, [persistCache]);

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 30000);
    return () => {
      clearInterval(interval);
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [fetchDashboardData]);

  const handleAddChart = () => {
    navigate("/dynamic-visualize");
  };

  const handleEditChart = (chartId) => {
    navigate(`/dynamic-visualize/${chartId}`);
  };

  const handleDeleteChart = async (chartId) => {
    if (!window.confirm("Are you sure you want to delete this chart?")) {
      return;
    }

    try {
      const response = await fetch(`${BACKEND_URL}/api/charts/${chartId}`, {
        method: "DELETE"
      });

      const data = await response.json();

      if (data.success) {
        setChartRecords((prev) => {
          const next = prev.filter((chart) => chart.id !== chartId);
          persistCache(next, connections);
          return next;
        });
        setStatusMessage(null);
      } else {
        setStatusMessage(data.error || "Failed to delete chart.");
      }
    } catch (err) {
      console.error("Error deleting chart:", err);
      setStatusMessage("Failed to delete chart. Please try again.");
    }
  };

  const handleDuplicateChart = async (chartId) => {
    try {
      const chartToDuplicate = normalizedCharts.find((chart) => chart.id === chartId);
      if (!chartToDuplicate) return;

      const duplicateConfig = {
        ...chartToDuplicate,
        title: `Copy of ${chartToDuplicate.title}`
      };

      delete duplicateConfig.id;

      const response = await fetch(`${BACKEND_URL}/api/charts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(duplicateConfig)
      });

      const data = await response.json();

      if (data.success) {
        await fetchDashboardData();
      } else {
        setStatusMessage(data.error || "Failed to duplicate chart.");
      }
    } catch (err) {
      console.error("Error duplicating chart:", err);
      setStatusMessage("Failed to duplicate chart. Please try again.");
    }
  };

  const handleRefresh = () => {
    fetchDashboardData();
  };

  const hasCharts = normalizedCharts.length > 0;
  const isLoading = loading;

  return (
    <div className="flex w-full flex-col gap-6 px-4 pb-10 pt-2 md:px-6 lg:px-8">
      {/* Header Section */}
      <section className="sticky top-[84px] z-30">
        <GlassCard className="sticky-edge shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Dynamic Dashboard</h1>
              <p className="text-xs text-slate-600 dark:text-slate-300">
                Real-time visualizations from your data connections.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="glass-hover rounded-full border border-emerald-400/40 bg-emerald-100/80 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 transition dark:border-emerald-300/20 dark:bg-emerald-500/20 dark:text-emerald-200">
                Live
              </div>
              <button 
                onClick={handleRefresh}
                className="glass-hover rounded-full border border-white/20 px-2.5 py-0.5 text-[11px] font-semibold text-slate-600 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-100 dark:border-slate-200/20 dark:text-slate-300 dark:hover:bg-white/5 dark:focus-visible:ring-offset-slate-900"
              >
                Refresh
              </button>
            </div>
          </div>
          <div className="mt-2 grid gap-2 md:grid-cols-3">
            {metrics.map((metric) => (
              <GlassCard key={metric.label} className="p-3 md:p-4 shadow-none">
                <p className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">{metric.label}</p>
                <div className="mt-1 text-xl font-semibold text-slate-900 dark:text-slate-100">{formatNumber(metric.value)}</div>
                <span className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold ${metric.color}`}>{metric.trend}</span>
              </GlassCard>
            ))}
          </div>
        </GlassCard>
      </section>

      {/* Status Messaging */}
      {statusMessage && (
        <div
          className={`rounded-xl border px-4 py-3 text-sm ${
            statusMessage.includes("cached")
              ? "border-amber-300 bg-amber-50/80 text-amber-700 dark:border-amber-400/40 dark:bg-amber-500/10 dark:text-amber-200"
              : "border-red-300 bg-red-50/80 text-red-700 dark:border-red-400/40 dark:bg-red-500/10 dark:text-red-200"
          }`}
          role="alert"
        >
          {statusMessage}
        </div>
      )}

      <AnomalyAlerts alerts={anomalyAlerts} connections={connections} onClear={clearAnomalyAlerts} />

      {/* Charts Section - Referenced from static dashboard */}
      <section aria-labelledby="saved-charts" className="mt-4 space-y-4 md:mt-6">
        <h2 id="saved-charts" className="sr-only">
          Saved charts
        </h2>
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">Saved charts</p>
            <p className="text-sm text-slate-500 dark:text-slate-300">Manage layouts, duplicate configurations, or jump back into edit mode.</p>
          </div>
          <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500 transition-colors dark:bg-slate-800/60 dark:text-slate-300">
            {normalizedCharts.length} charts
          </div>
        </header>

        {isLoading ? (
          <div className="flex h-64 items-center justify-center rounded-2xl border border-dashed border-white/30 dark:border-slate-200/20">
            <div className="text-center">
              <div className="mb-2 h-8 w-8 animate-spin rounded-full border-4 border-slate-300 border-t-blue-500 mx-auto"></div>
              <p className="text-slate-500 dark:text-slate-400">Loading charts...</p>
            </div>
          </div>
        ) : !hasCharts ? (
          <GlassCard className="glass-hover flex h-48 flex-col items-center justify-center border border-dashed border-white/40 text-sm text-slate-600 transition-colors dark:border-slate-200/30 dark:text-slate-300">
            <p className="mb-3">No charts yet. Create your first visualization.</p>
            <button 
              onClick={handleAddChart} 
              className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-600 flex items-center gap-2"
            >
              <span className="text-lg">+</span> New Chart
            </button>
          </GlassCard>
        ) : (
          <div className="grid gap-4 sm:gap-6 lg:grid-cols-2">
            {normalizedCharts
              .sort((a, b) => new Date(b.updatedAt || 0).getTime() - new Date(a.updatedAt || 0).getTime())
              .map((chart) => (
                <ChartCard
                  key={chart.id}
                  chart={chart}
                  connections={connections}
                  onEdit={handleEditChart}
                  onDuplicate={handleDuplicateChart}
                  onDelete={handleDeleteChart}
                />
              ))}
            <GlassCard className="flex h-[300px] flex-col items-center justify-center gap-3 border-2 border-dashed border-white/40 text-center shadow-xl dark:border-slate-200/30">
              <div className="rounded-full bg-brand-100 p-4 dark:bg-brand-500/20">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-brand-500">
                  <path d="M12 5v14M5 12h14"></path>
                </svg>
              </div>
              <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">Create New Chart</h3>
              <p className="text-xs text-center text-slate-500 dark:text-slate-300">
                Add a new visualization to your dashboard
              </p>
              <button
                onClick={handleAddChart}
                className="mt-2 rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-600"
              >
                New Chart
              </button>
            </GlassCard>
          </div>
        )}
      </section>
    </div>
  );
}
