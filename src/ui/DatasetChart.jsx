import { useMemo } from "react";
import ChartRenderer from "./ChartRenderer.jsx";
import { buildChartData } from "../utils/chartData.js";
import { useDatasetRows } from "../hooks/useDatasetRows.js";

export function ChartStatus({ children, compact = false, tone = "muted" }) {
  return (
    <div
      className={`flex w-full items-center justify-center px-4 text-center text-sm ${compact ? "h-[280px]" : "h-[340px]"} ${
        tone === "error" ? "text-red-500 dark:text-red-400" : "text-slate-500 dark:text-slate-400"
      }`}
    >
      {children}
    </div>
  );
}

export function TruncationNote({ shown, total }) {
  return (
    <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
      Showing the first {shown.toLocaleString()} of {total.toLocaleString()} rows.
    </p>
  );
}

// ChartRenderer plus row loading for a saved dataset
export default function DatasetChart({ chart, dataset, compact = false, ...rendererProps }) {
  const { rows, loading, error, total, truncated } = useDatasetRows(dataset);
  const data = useMemo(
    () => buildChartData(rows, chart.chartType, chart.mappings, chart.options || {}),
    [rows, chart.chartType, chart.mappings, chart.options]
  );

  if (loading) return <ChartStatus compact={compact}>Loading data…</ChartStatus>;
  if (error) {
    return (
      <ChartStatus compact={compact} tone="error">
        Couldn&apos;t load data: {error.message}
      </ChartStatus>
    );
  }
  return (
    <>
      <ChartRenderer chart={chart} data={data} compact={compact} {...rendererProps} />
      {truncated ? <TruncationNote shown={rows.length} total={total} /> : null}
    </>
  );
}
