import { useEffect, useState } from "react";
import { fetchDatasetRows, MAX_CHART_ROWS } from "../utils/datasetApi.js";

const EMPTY = [];

// In-flight and finished loads, keyed by dataset id + version, shared by every chart
const pending = new Map();
const loaded = new Map();

const versionKey = (dataset) => `${dataset.id}:${dataset.updatedAt || ""}`;

function loadRows(dataset) {
  const key = versionKey(dataset);
  if (!pending.has(key)) {
    const request = fetchDatasetRows(dataset.id, { limit: MAX_CHART_ROWS })
      .then((body) => {
        const result = { rows: body.rows || EMPTY, total: body.total ?? 0 };
        if (result.total > result.rows.length) {
          console.warn(
            `Dataset "${dataset.name}" has ${result.total.toLocaleString()} rows; charts use the first ${result.rows.length.toLocaleString()}.`
          );
        }
        loaded.set(key, result);
        return result;
      })
      .catch((error) => {
        pending.delete(key); // allow a retry on the next render
        throw error;
      });
    pending.set(key, request);
  }
  return pending.get(key);
}

export function forgetDatasetRows(datasetId) {
  for (const map of [pending, loaded]) {
    for (const key of map.keys()) {
      if (key.startsWith(`${datasetId}:`)) map.delete(key);
    }
  }
}

/**
 * Rows for a dataset from the store.
 * - Datasets saved by the data service (have `storagePath`): fetched from the API, up to 50,000 rows.
 * - Older Firestore datasets and demo-mode datasets: their inline `data`, else `rowsPreview`.
 */
export function useDatasetRows(dataset) {
  const remote = Boolean(dataset?.storagePath);
  const key = remote ? versionKey(dataset) : null;
  const [state, setState] = useState({ key: null, rows: EMPTY, total: 0, error: null });

  useEffect(() => {
    if (!key || loaded.has(key)) return undefined;
    let cancelled = false;
    loadRows(dataset)
      .then(({ rows, total }) => !cancelled && setState({ key, rows, total, error: null }))
      .catch((error) => !cancelled && setState({ key, rows: EMPTY, total: 0, error }));
    return () => {
      cancelled = true;
    };
    // `key` changes whenever the dataset id or version changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!dataset) return { rows: EMPTY, total: 0, loading: false, error: null, truncated: false };

  if (!remote) {
    const rows = Array.isArray(dataset.data) ? dataset.data : dataset.rowsPreview || EMPTY;
    return { rows, total: rows.length, loading: false, error: null, truncated: false };
  }

  const cached = loaded.get(key);
  if (cached) {
    return { rows: cached.rows, total: cached.total, loading: false, error: null, truncated: cached.total > cached.rows.length };
  }
  if (state.key === key && state.error) {
    return { rows: EMPTY, total: 0, loading: false, error: state.error, truncated: false };
  }
  return { rows: EMPTY, total: dataset.originalRowCount ?? 0, loading: true, error: null, truncated: false };
}
