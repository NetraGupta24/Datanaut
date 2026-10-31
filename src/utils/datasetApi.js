// Client for the DATANAUT data service (chatbot-service, FastAPI).
// Every request carries the signed-in user's Firebase ID token.
import { auth } from "../firebase.js";

export const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

// Charts load at most this many rows per dataset (the API's hard limit)
export const MAX_CHART_ROWS = 50000;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const readErrorDetail = async (res) => {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d) => d.msg).filter(Boolean).join("; ");
  } catch {
    // not JSON
  }
  return "";
};

export async function apiFetch(path, { timeoutMs = 60000, headers, ...options } = {}) {
  const user = auth?.currentUser;
  if (!user) throw new ApiError("You need to be signed in.", 401);
  const token = await user.getIdToken();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: { ...headers, Authorization: `Bearer ${token}` },
      signal: controller.signal
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new ApiError("The data service took too long to respond. Please try again.", 0);
    }
    throw new ApiError(`Can't reach the data service at ${API_URL}. Is it running?`, 0);
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    if (res.status === 401) throw new ApiError("Your session has expired. Please sign in again.", 401);
    const detail = await readErrorDetail(res);
    throw new ApiError(detail || `Request failed (${res.status}).`, res.status);
  }
  return res.json();
}

export function uploadDatasetFile({ file, name, sourceType = "file", firstRowHeader = true }) {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("name", name);
  form.append("source_type", sourceType);
  form.append("first_row_header", firstRowHeader ? "true" : "false");
  return apiFetch("/datasets", { method: "POST", body: form, timeoutMs: 120000 });
}

export function fetchDatasetRows(datasetId, { limit = MAX_CHART_ROWS, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiFetch(`/datasets/${encodeURIComponent(datasetId)}/rows?${params}`);
}

export function deleteDatasetRemote(datasetId) {
  return apiFetch(`/datasets/${encodeURIComponent(datasetId)}`, { method: "DELETE" });
}
