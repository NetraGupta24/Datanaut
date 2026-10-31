import test from "node:test";
import assert from "node:assert/strict";
import { AnomalyDetector, anomalyOptionsFromEnv } from "./anomalyDetector.js";

// Steady readings that alternate around 20 (mean 20, small spread)
const warmUp = (detector, stream, count = 20, field = "temperature") => {
  for (let i = 0; i < count; i += 1) {
    detector.check(stream, { timestamp: `t${i}`, [field]: 20 + (i % 2 ? 0.5 : -0.5) });
  }
};

test("flags a spike with |z| > 3 and reports the score", () => {
  const detector = new AnomalyDetector();
  warmUp(detector, "conn1|/sensor-data|sim");
  const result = detector.check("conn1|/sensor-data|sim", { timestamp: "spike", temperature: 30, device_id: "sim" });
  assert.equal(result.anomaly, true);
  assert.equal(result.anomalies.length, 1);
  const [hit] = result.anomalies;
  assert.equal(hit.field, "temperature");
  assert.equal(hit.value, 30);
  assert.equal(hit.mean, 20);
  assert.ok(hit.score > 3 && result.score === hit.score);
});

test("normal readings are not flagged but still get a score", () => {
  const detector = new AnomalyDetector();
  warmUp(detector, "s");
  const result = detector.check("s", { temperature: 20.4 });
  assert.equal(result.anomaly, false);
  assert.deepEqual(result.anomalies, []);
  assert.ok(Math.abs(result.score) < 1);
});

test("stays quiet until the window has enough history", () => {
  const detector = new AnomalyDetector({ minSamples: 10 });
  warmUp(detector, "s", 5);
  const result = detector.check("s", { temperature: 1000 });
  assert.deepEqual(result, { anomaly: false, score: null, anomalies: [] });
});

test("windows are separate per stream and per field, and keep only the last N values", () => {
  const detector = new AnomalyDetector({ windowSize: 50 });
  warmUp(detector, "conn1|a", 60);
  warmUp(detector, "conn1|a", 20, "humidity");
  assert.equal(detector.windows.get("conn1|a|temperature").length, 50);
  assert.equal(detector.windows.get("conn1|a|humidity").length, 20);
  // conn2 has no history, so the same spike is not judged there yet
  assert.equal(detector.check("conn2|a", { temperature: 30 }).anomaly, false);
  assert.equal(detector.check("conn1|a", { temperature: 30 }).anomaly, true);
});

test("the threshold is configurable", () => {
  const strict = new AnomalyDetector({ threshold: 1 });
  warmUp(strict, "s");
  assert.equal(strict.check("s", { temperature: 21 }).anomaly, true);

  const loose = new AnomalyDetector({ threshold: 50 });
  warmUp(loose, "s");
  assert.equal(loose.check("s", { temperature: 30 }).anomaly, false);
});

test("ignores ids, timestamps, text and constant fields; parses numeric strings", () => {
  const detector = new AnomalyDetector();
  for (let i = 0; i < 20; i += 1) {
    detector.check("s", { id: i, ts: Date.now(), status: "ok", flat: 5, pressure: String(1000 + (i % 2)) });
  }
  const result = detector.check("s", { id: 999, ts: 0, status: "ok", flat: 6, pressure: "1050" });
  assert.deepEqual(result.anomalies.map((a) => a.field), ["pressure"]);
  assert.ok(!detector.windows.has("s|id") && !detector.windows.has("s|status"));
});

test("forget drops one connection's history", () => {
  const detector = new AnomalyDetector();
  warmUp(detector, "conn1|x");
  warmUp(detector, "conn10|x");
  detector.forget("conn1|");
  assert.ok(!detector.windows.has("conn1|x|temperature"));
  assert.ok(detector.windows.has("conn10|x|temperature"));
});

test("options come from ANOMALY_WINDOW and ANOMALY_Z_THRESHOLD with safe defaults", () => {
  assert.deepEqual(anomalyOptionsFromEnv({}), { windowSize: 50, threshold: 3 });
  assert.deepEqual(anomalyOptionsFromEnv({ ANOMALY_WINDOW: "100", ANOMALY_Z_THRESHOLD: "2.5" }), { windowSize: 100, threshold: 2.5 });
  assert.deepEqual(anomalyOptionsFromEnv({ ANOMALY_WINDOW: "-4", ANOMALY_Z_THRESHOLD: "abc" }), { windowSize: 50, threshold: 3 });
});
