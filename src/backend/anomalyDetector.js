// Rolling z-score anomaly detection for live readings.
// Each numeric field of each stream keeps a window of its recent values; a new value is
// flagged when it is more than `threshold` standard deviations from that window's mean.

// Identifiers and timestamps are not measurements
const META_FIELDS = new Set(["timestamp", "time", "ts", "id", "device_id", "deviceId", "endpoint", "topic"]);

const toNumber = (value) => {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const round = (value) => Math.round(value * 1000) / 1000;

const positiveNumber = (raw, fallback) => {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

export const anomalyOptionsFromEnv = (env = process.env) => ({
  windowSize: Math.round(positiveNumber(env.ANOMALY_WINDOW, 50)),
  threshold: positiveNumber(env.ANOMALY_Z_THRESHOLD, 3)
});

export class AnomalyDetector {
  constructor({ windowSize = 50, threshold = 3, minSamples = 10 } = {}) {
    this.windowSize = Math.max(2, windowSize);
    this.threshold = threshold;
    // Too few values give a meaningless standard deviation, so stay quiet until the window has some history
    this.minSamples = Math.min(Math.max(2, minSamples), this.windowSize);
    this.windows = new Map();
  }

  // Score every numeric field of `row` against its window, then add the reading to the window.
  // Returns { anomaly, score, anomalies }: score is the z-score furthest from 0 (null without enough history).
  check(streamKey, row) {
    const anomalies = [];
    let top = null;
    for (const [field, raw] of Object.entries(row || {})) {
      if (META_FIELDS.has(field)) continue;
      const value = toNumber(raw);
      if (value === null) continue;

      const key = `${streamKey}|${field}`;
      const history = this.windows.get(key) || [];
      if (history.length >= this.minSamples) {
        const mean = history.reduce((sum, v) => sum + v, 0) / history.length;
        const variance = history.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (history.length - 1);
        const std = Math.sqrt(variance);
        if (std > 0) {
          const score = (value - mean) / std;
          if (top === null || Math.abs(score) > Math.abs(top)) top = score;
          if (Math.abs(score) > this.threshold) {
            anomalies.push({ field, value, score: round(score), mean: round(mean), std: round(std) });
          }
        }
      }
      history.push(value);
      if (history.length > this.windowSize) history.shift();
      this.windows.set(key, history);
    }
    return { anomaly: anomalies.length > 0, score: top === null ? null : round(top), anomalies };
  }

  // Drop the history of every stream whose key starts with `prefix`, e.g. a removed connection
  forget(prefix) {
    for (const key of this.windows.keys()) {
      if (key.startsWith(prefix)) this.windows.delete(key);
    }
  }
}
