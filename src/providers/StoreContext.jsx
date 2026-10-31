import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { parseCSVText, parseJSONText } from "../utils/parseData.js";
import { deleteDatasetRemote, uploadDatasetFile } from "../utils/datasetApi.js";
import { forgetDatasetRows } from "../hooks/useDatasetRows.js";
import { db, DEMO_MODE } from "../firebase.js";
import { useAuth } from "./AuthContext.jsx";
import {
  collection,
  doc,
  setDoc,
  deleteDoc,
  onSnapshot,
  writeBatch
} from "firebase/firestore";

const StoreContext = createContext(null);

const DATASETS_KEY = "datasets";
const CHARTS_KEY = "charts";
const SEED_FLAG_KEY = "seeded";

const nowIso = () => new Date().toISOString();

const SAMPLE_FILES = [
  { path: "/samples/sample_sales.csv", name: "Sample Sales", sourceType: "sample_csv" },
  { path: "/samples/sample_scatter.csv", name: "Sample Scatter", sourceType: "sample_csv" },
  { path: "/samples/sample_pie.json", name: "Sample Categories", sourceType: "sample_json" }
];

const sampleCharts = (salesId, scatterId, pieId) => [
  { title: "Revenue by Month", datasetId: salesId, chartType: "bar", mappings: { xField: "Month", yFields: ["Revenue"], stacked: false }, options: { aggregation: "none", topN: 0 } },
  { title: "Units vs Revenue", datasetId: scatterId, chartType: "scatter", mappings: { xField: "Units", yField: "Revenue" }, options: { aggregation: "none", topN: 0 } },
  { title: "Share by Category", datasetId: pieId, chartType: "donut", mappings: { categoryField: "category", valueField: "value", donut: true }, options: { aggregation: "sum", topN: 0 } }
];

export const generateId = () => {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `id_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
};

const safeParse = (value) => {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    console.warn("Failed to parse localStorage value", error);
    return {};
  }
};

export function StoreProvider({ children }) {
  const { user } = useAuth();
  const [datasets, setDatasets] = useState({});
  const [charts, setCharts] = useState({});
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);
  const [seeded, setSeeded] = useState(false);
  // One seeding attempt per user per session, so a failing API isn't retried in a loop
  const seedAttempted = useRef(false);

  // Demo users have no real Firebase session, so their data stays in localStorage.
  // Signed-in users keep metadata in Firestore and full rows in the data service.
  const isFirestoreAvailable = !!db && !!user && !DEMO_MODE;

  // ─── Load data ───────────────────────────────────────────────
  useEffect(() => {
    seedAttempted.current = false;
    if (!isFirestoreAvailable) {
      // Not logged in (or demo mode) — use localStorage
      const storedDatasets = safeParse(localStorage.getItem(DATASETS_KEY));
      const storedCharts = safeParse(localStorage.getItem(CHARTS_KEY));
      const seededFlag = localStorage.getItem(SEED_FLAG_KEY) === "true";
      setDatasets(storedDatasets);
      setCharts(storedCharts);
      setSeeded(seededFlag);
      setLoading(false);
      return;
    }

    // Logged in — listen to Firestore in real time
    setLoading(true);
    const datasetsRef = collection(db, "users", user.uid, "datasets");
    const chartsRef = collection(db, "users", user.uid, "charts");

    let datasetsLoaded = false;
    let chartsLoaded = false;

    const checkDone = () => {
      if (datasetsLoaded && chartsLoaded) setLoading(false);
    };

    const unsubDatasets = onSnapshot(datasetsRef, (snap) => {
      const data = {};
      snap.forEach((d) => { data[d.id] = d.data(); });
      setDatasets(data);
      datasetsLoaded = true;
      checkDone();
    });

    const unsubCharts = onSnapshot(chartsRef, (snap) => {
      const data = {};
      snap.forEach((d) => { data[d.id] = d.data(); });
      setCharts(data);
      chartsLoaded = true;
      checkDone();
    });

    return () => {
      unsubDatasets();
      unsubCharts();
    };
  }, [user, isFirestoreAvailable]);

  // ─── Sync to localStorage when not using Firestore ───────────
  useEffect(() => {
    if (!loading && !isFirestoreAvailable) {
      localStorage.setItem(DATASETS_KEY, JSON.stringify(datasets));
    }
  }, [datasets, loading, isFirestoreAvailable]);

  useEffect(() => {
    if (!loading && !isFirestoreAvailable) {
      localStorage.setItem(CHARTS_KEY, JSON.stringify(charts));
    }
  }, [charts, loading, isFirestoreAvailable]);

  // ─── Seed samples ─────────────────────────────────────────────
  // Signed in: upload the sample files to the data service, then save charts pointing at them
  const seedSamplesRemote = useCallback(async () => {
    const created = [];
    for (const sample of SAMPLE_FILES) {
      const blob = await (await fetch(sample.path)).blob();
      const file = new File([blob], sample.path.split("/").pop(), { type: blob.type });
      const meta = await uploadDatasetFile({ file, name: sample.name, sourceType: sample.sourceType });
      created.push(meta);
      setDatasets((prev) => ({ ...prev, [meta.id]: meta }));
    }
    const chartNow = nowIso();
    const batch = writeBatch(db);
    sampleCharts(...created.map((meta) => meta.id)).forEach((item) => {
      const id = generateId();
      batch.set(doc(db, "users", user.uid, "charts", id), { id, ...item, createdAt: chartNow, updatedAt: chartNow });
    });
    await batch.commit();
  }, [user]);

  const seedSamples = useCallback(async () => {
    if (seeding || seedAttempted.current) return;
    seedAttempted.current = true;
    setSeeding(true);
    try {
      if (isFirestoreAvailable) {
        await seedSamplesRemote();
        setSeeded(true);
        return;
      }

      const [salesResp, scatterResp, pieResp] = await Promise.all([
        fetch("/samples/sample_sales.csv"),
        fetch("/samples/sample_scatter.csv"),
        fetch("/samples/sample_pie.json")
      ]);
      const [salesText, scatterText, pieText] = await Promise.all([
        salesResp.text(),
        scatterResp.text(),
        pieResp.text()
      ]);

      const salesParsed = await parseCSVText(salesText, { firstRowHeader: true });
      const scatterParsed = await parseCSVText(scatterText, { firstRowHeader: true });
      const pieParsed = parseJSONText(pieText);

      const salesId = generateId();
      const scatterId = generateId();
      const pieId = generateId();
      const chartNow = nowIso();

      const seededDatasets = {
        [salesId]: { id: salesId, name: "Sample Sales", sourceType: "sample_csv", schema: { headers: salesParsed.headers, types: salesParsed.types }, rowsPreview: salesParsed.rows.slice(0, 50), data: salesParsed.rows, createdAt: chartNow, updatedAt: chartNow },
        [scatterId]: { id: scatterId, name: "Sample Scatter", sourceType: "sample_csv", schema: { headers: scatterParsed.headers, types: scatterParsed.types }, rowsPreview: scatterParsed.rows.slice(0, 50), data: scatterParsed.rows, createdAt: chartNow, updatedAt: chartNow },
        [pieId]: { id: pieId, name: "Sample Categories", sourceType: "sample_json", schema: { headers: pieParsed.headers, types: pieParsed.types }, rowsPreview: pieParsed.rows.slice(0, 50), data: pieParsed.rows, createdAt: chartNow, updatedAt: chartNow }
      };

      const seededCharts = sampleCharts(salesId, scatterId, pieId).reduce((acc, item) => {
        const id = generateId();
        acc[id] = { id, ...item, createdAt: chartNow, updatedAt: chartNow };
        return acc;
      }, {});

      setDatasets(seededDatasets);
      setCharts(seededCharts);
      localStorage.setItem(SEED_FLAG_KEY, "true");
      setSeeded(true);
    } catch (error) {
      console.error("Failed to seed samples", error);
    } finally {
      setSeeding(false);
    }
  }, [seeding, isFirestoreAvailable, seedSamplesRemote]);

  useEffect(() => {
    if (!loading && !seeded && Object.keys(datasets).length === 0 && Object.keys(charts).length === 0) {
      seedSamples();
    }
  }, [datasets, charts, loading, seedSamples, seeded]);

  // ─── CRUD operations ──────────────────────────────────────────
  // Local (demo) datasets only; signed-in users go through uploadDataset
  const saveDataset = useCallback(async (dataset) => {
    if (isFirestoreAvailable) {
      await setDoc(doc(db, "users", user.uid, "datasets", dataset.id), dataset);
    } else {
      setDatasets((prev) => ({ ...prev, [dataset.id]: dataset }));
    }
  }, [isFirestoreAvailable, user]);

  // Sends the original file to the data service, which stores the full table and
  // writes the metadata doc; the Firestore listener then keeps it in sync.
  const uploadDataset = useCallback(async ({ file, name, sourceType, firstRowHeader }) => {
    if (!isFirestoreAvailable) {
      throw new Error("Uploading to the data service requires a signed-in Firebase user.");
    }
    const meta = await uploadDatasetFile({ file, name, sourceType, firstRowHeader });
    setDatasets((prev) => ({ ...prev, [meta.id]: meta }));
    return meta;
  }, [isFirestoreAvailable]);

  const deleteDataset = useCallback(async (datasetId) => {
    if (isFirestoreAvailable) {
      // The service removes the stored file, the metadata doc and linked charts
      await deleteDatasetRemote(datasetId);
      forgetDatasetRows(datasetId);
    } else {
      setDatasets((prev) => { const next = { ...prev }; delete next[datasetId]; return next; });
      setCharts((prev) => {
        const next = { ...prev };
        Object.values(prev).forEach((chart) => { if (chart.datasetId === datasetId) delete next[chart.id]; });
        return next;
      });
    }
  }, [isFirestoreAvailable]);

  const saveChart = useCallback(async (chart) => {
    if (isFirestoreAvailable) {
      await setDoc(doc(db, "users", user.uid, "charts", chart.id), chart);
    } else {
      setCharts((prev) => ({ ...prev, [chart.id]: chart }));
    }
  }, [isFirestoreAvailable, user]);

  const duplicateChart = useCallback(async (chartId) => {
    const original = charts[chartId];
    if (!original) return;
    const clonedId = generateId();
    const clone = { ...original, id: clonedId, title: `${original.title} (Copy)`, createdAt: nowIso(), updatedAt: nowIso() };
    await saveChart(clone);
  }, [charts, saveChart]);

  const deleteChart = useCallback(async (chartId) => {
    if (isFirestoreAvailable) {
      await deleteDoc(doc(db, "users", user.uid, "charts", chartId));
    } else {
      setCharts((prev) => { const next = { ...prev }; delete next[chartId]; return next; });
    }
  }, [isFirestoreAvailable, user]);

  const value = useMemo(() => ({
    datasets, charts, loading,
    usesServerStorage: isFirestoreAvailable,
    saveDataset, uploadDataset, deleteDataset,
    saveChart, deleteChart,
    duplicateChart, generateId
  }), [charts, datasets, deleteChart, deleteDataset, duplicateChart, isFirestoreAvailable, loading, saveChart, saveDataset, uploadDataset]);

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
  const context = useContext(StoreContext);
  if (!context) throw new Error("useStore must be used within StoreProvider");
  return context;
}