// Sends fake sensor readings to POST /api/sensor-data so the live dashboard and anomaly
// alerts can be demoed without hardware. Readings drift smoothly with a little noise;
// now and then one field gets a spike that the backend should flag as an anomaly.
//
//   node scripts/sensorSimulator.js [--url http://localhost:8085] [--device sim-01]
//        [--interval 1000] [--spike-chance 0.03] [--count 0] [--skip-setup]
//
// Unless --skip-setup is given, it first makes sure the backend has an HTTP connection named
// "Sensor simulator" and a live line chart of its temperature, so the Dynamic Dashboard shows it.

import { parseArgs } from "node:util";

const { values: opts } = parseArgs({
  options: {
    url: { type: "string", default: "http://localhost:8085" },
    device: { type: "string", default: "sim-01" },
    interval: { type: "string", default: "1000" },
    "spike-chance": { type: "string", default: "0.03" },
    count: { type: "string", default: "0" },
    "skip-setup": { type: "boolean", default: false }
  }
});

const baseUrl = opts.url.replace(/\/+$/, "");
const intervalMs = Math.max(100, Number(opts.interval) || 1000);
const spikeChance = Math.min(1, Math.max(0, Number(opts["spike-chance"]) || 0));
const maxReadings = Math.max(0, Number(opts.count) || 0);
const CONNECTION_NAME = "Sensor simulator";

// Spikes hit temperature most often because that is what the demo chart shows
const SPIKES = [
  { field: "temperature", weight: 0.5, min: 8, max: 12 },
  { field: "humidity", weight: 0.3, min: 20, max: 30 },
  { field: "pressure", weight: 0.2, min: 15, max: 25 }
];

const between = (min, max) => min + Math.random() * (max - min);
const round2 = (value) => Math.round(value * 100) / 100;

async function api(path, init) {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) }
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.success === false) {
    throw new Error(body.error || `${response.status} ${response.statusText}`);
  }
  return body;
}

async function setUp() {
  const { connections = [] } = await api("/api/connections");
  let connection = connections.find((c) => c.type === "http" && c.config?.name === CONNECTION_NAME);
  if (!connection) {
    const created = await api("/api/add-connection", {
      method: "POST",
      body: JSON.stringify({ type: "http", config: { name: CONNECTION_NAME, url: baseUrl } })
    });
    connection = { id: created.id };
    console.log(`Created HTTP connection "${CONNECTION_NAME}" (${connection.id}).`);
  }

  const { charts = [] } = await api("/api/charts");
  if (!charts.some((chart) => chart.dataSource === connection.id)) {
    await api("/api/charts", {
      method: "POST",
      body: JSON.stringify({
        title: "Simulated temperature",
        type: "line",
        dataSource: connection.id,
        dimension: "2d",
        xField: "timestamp",
        yField: "temperature",
        options: { aggregation: "none", topN: 0, dimension: "2d" }
      })
    });
    console.log(`Created live chart "Simulated temperature" on ${connection.id}.`);
  }
}

function makeReading(index) {
  const t = (index * intervalMs) / 1000;
  const reading = {
    device_id: opts.device,
    timestamp: new Date().toISOString(),
    temperature: 22 + 1.5 * Math.sin((2 * Math.PI * t) / 120) + between(-0.3, 0.3),
    humidity: 45 + 4 * Math.sin((2 * Math.PI * t) / 300 + 1) + between(-1, 1),
    pressure: 1013 + between(-0.8, 0.8)
  };

  let spike = null;
  // No spikes during the first readings: the backend needs some history before it can judge them
  if (index >= 15 && Math.random() < spikeChance) {
    let pick = Math.random();
    spike = SPIKES.find((s) => (pick -= s.weight) < 0) || SPIKES[0];
    reading[spike.field] += (Math.random() < 0.5 ? -1 : 1) * between(spike.min, spike.max);
  }
  for (const s of SPIKES) reading[s.field] = round2(reading[s.field]);
  return { reading, spike };
}

async function main() {
  console.log(`Sending readings for "${opts.device}" to ${baseUrl}/api/sensor-data every ${intervalMs} ms (spike chance ${spikeChance}).`);
  if (!opts["skip-setup"]) {
    try {
      await setUp();
    } catch (err) {
      console.error(`Setup failed (is the backend running on ${baseUrl}?): ${err.message}`);
      process.exit(1);
    }
  }

  let index = 0;
  let spikes = 0;
  const timer = setInterval(async () => {
    const current = index;
    index += 1;
    if (maxReadings && current >= maxReadings) {
      clearInterval(timer);
      console.log(`Done: ${maxReadings} readings, ${spikes} spikes.`);
      return;
    }
    const { reading, spike } = makeReading(current);
    try {
      await api("/api/sensor-data", { method: "POST", body: JSON.stringify(reading) });
      if (spike) {
        spikes += 1;
        console.log(`⚡ Spike #${spikes}: ${spike.field} = ${reading[spike.field]} at ${reading.timestamp}`);
      } else if (current % 10 === 0) {
        console.log(`#${current} temperature ${reading.temperature}, humidity ${reading.humidity}, pressure ${reading.pressure}`);
      }
    } catch (err) {
      console.error(`#${current} not sent: ${err.message}`);
    }
  }, intervalMs);

  process.on("SIGINT", () => {
    clearInterval(timer);
    console.log(`\nStopped after ${index} readings, ${spikes} spikes.`);
    process.exit(0);
  });
}

main();
