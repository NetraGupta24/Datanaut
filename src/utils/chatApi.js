// Chat goes through the DATANAUT data service (POST /chat). The LLM key lives only on
// the server; the browser sends the user's Firebase ID token.
import { ApiError, apiFetch } from "./datasetApi.js";

export const MAX_HISTORY_MESSAGES = 10;
const CHAT_TIMEOUT_MS = 90000;

const friendlyError = (error) => {
  if (!(error instanceof ApiError)) return error;
  if (error.status === 401) return new ApiError("Your session has expired. Please sign in again to keep chatting.", 401);
  if (error.status === 429) {
    return new ApiError("You're sending messages quickly. Please wait a minute and try again.", 429);
  }
  if (error.code === "timeout") {
    return new ApiError("The assistant took too long to answer. Try a simpler question or ask again.", 0, "timeout");
  }
  return error;
};

/** Returns { answer, evidence: [{ tool, args, result_table: { columns, rows, note } }] }. */
export async function sendChatMessage({ datasetId, message, history = [] }) {
  try {
    return await apiFetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: datasetId,
        message,
        history: history.slice(-MAX_HISTORY_MESSAGES)
      }),
      timeoutMs: CHAT_TIMEOUT_MS
    });
  } catch (error) {
    throw friendlyError(error);
  }
}

const signed = (value) => (typeof value === "number" && value > 0 ? `+${value}` : `${value}`);
const num = (value) => (typeof value === "number" ? value.toLocaleString() : value ?? "n/a");

/** Readable version of the profile the data service stored with the dataset. */
export function formatProfile(dataset) {
  const profile = dataset?.profile;
  if (!profile) {
    return `No profile is stored for "${dataset?.name || "this dataset"}". Re-upload it on the Data page to get one.`;
  }
  const lines = [
    `📊 ${dataset.name}: ${num(profile.rows)} rows × ${profile.cols} columns, ${num(profile.duplicate_rows)} duplicate rows`,
    ""
  ];
  Object.entries(profile.columns || {}).forEach(([name, col]) => {
    const missing = col.missing_pct ? `, ${col.missing_pct}% missing` : "";
    if (col.type === "number") {
      lines.push(
        `• ${name} (number): mean ${num(col.mean)}, median ${num(col.median)}, min ${num(col.min)}, max ${num(col.max)}` +
          `${col.outliers ? `, ${col.outliers} outliers` : ""}${missing}`
      );
    } else if (col.type === "date") {
      const trends = (col.trend || []).map((t) => `${t.metric} ${t.direction} (${signed(t.change_pct)}%)`).join(", ");
      lines.push(
        `• ${name} (date): ${col.min} → ${col.max}${col.frequency ? `, ${col.frequency}` : ""}` +
          `${trends ? `; trend: ${trends}` : ""}${missing}`
      );
    } else {
      const top = (col.top || []).slice(0, 3).map((t) => `${t.value} (${t.pct}%)`).join(", ");
      lines.push(`• ${name} (text): ${num(col.unique)} unique${top ? `; top: ${top}` : ""}${missing}`);
    }
  });
  if (profile.top_correlations?.length) {
    lines.push("", "Strongest correlations: " + profile.top_correlations.map((c) => `${c.a} ~ ${c.b} (r=${c.r})`).join(", "));
  }
  return lines.join("\n");
}
