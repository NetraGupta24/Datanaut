import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { parseCSVText, parseJSONText } from "../utils/parseData.js";
import { db } from "../firebase.js";
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

  const isFirestoreAvailable = !!db && !!user;

  // ─── Load data ───────────────────────────────────────────────
  useEffect(() => {
    if (!user) {
      // Not logged in — use localStorage
      const storedDatasets = safeParse(localStorage.getItem(DATASETS_KEY));
      const storedCharts = safeParse(localStorage.getItem(CHARTS_KEY));
      const seededFlag = localStorage.getItem(SEED_FLAG_KEY) === "true";
      setDatasets(storedDatasets);
      setCharts(storedCharts);
      setSeeded(seededFlag);
      setLoading(false);
      return;
    }

    if (!db) {
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
  }, [user]);

  // ─── Sync to localStorage when not using Firestore ───────────
  useEffect(() => {
    if (!loading && !user) {
      localStorage.setItem(DATASETS_KEY, JSON.stringify(datasets));
    }
  }, [datasets, loading, user]);

  useEffect(() => {
    if (!loading && !user) {
      localStorage.setItem(CHARTS_KEY, JSON.stringify(charts));
    }
  }, [charts, loading, user]);

  // ─── Seed samples ─────────────────────────────────────────────
  const seedSamples = useCallback(async () => {
    if (seeding) return;
    setSeeding(true);
    try {
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

      const seededCharts = [
        { title: "Revenue by Month", datasetId: salesId, chartType: "bar", mappings: { xField: "Month", yFields: ["Revenue"], stacked: false }, options: { aggregation: "none", topN: 0 } },
        { title: "Units vs Revenue", datasetId: scatterId, chartType: "scatter", mappings: { xField: "Units", yField: "Revenue" }, options: { aggregation: "none", topN: 0 } },
        { title: "Share by Category", datasetId: pieId, chartType: "donut", mappings: { categoryField: "category", valueField: "value", donut: true }, options: { aggregation: "sum", topN: 0 } }
      ].reduce((acc, item) => {
        const id = generateId();
        acc[id] = { id, ...item, createdAt: chartNow, updatedAt: chartNow };
        return acc;
      }, {});

      if (isFirestoreAvailable) {
        const batch = writeBatch(db);
        Object.values(seededDatasets).forEach((ds) => {
          batch.set(doc(db, "users", user.uid, "datasets", ds.id), ds);
        });
        Object.values(seededCharts).forEach((ch) => {
          batch.set(doc(db, "users", user.uid, "charts", ch.id), ch);
        });
        await batch.commit();
      } else {
        setDatasets(seededDatasets);
        setCharts(seededCharts);
        localStorage.setItem(SEED_FLAG_KEY, "true");
      }
      setSeeded(true);
    } catch (error) {
      console.error("Failed to seed samples", error);
    } finally {
      setSeeding(false);
    }
  }, [seeding, isFirestoreAvailable, user]);

  useEffect(() => {
    if (!loading && !seeded && Object.keys(datasets).length === 0 && Object.keys(charts).length === 0) {
      seedSamples();
    }
  }, [datasets, charts, loading, seedSamples, seeded]);

  // ─── CRUD operations ──────────────────────────────────────────
  const saveDataset = useCallback(async (dataset) => {
    if (isFirestoreAvailable) {
      await setDoc(doc(db, "users", user.uid, "datasets", dataset.id), dataset);
    } else {
      setDatasets((prev) => ({ ...prev, [dataset.id]: dataset }));
    }
  }, [isFirestoreAvailable, user]);

  const deleteDataset = useCallback(async (datasetId) => {
    if (isFirestoreAvailable) {
      await deleteDoc(doc(db, "users", user.uid, "datasets", datasetId));
      // delete linked charts
      const linkedCharts = Object.values(charts).filter(c => c.datasetId === datasetId);
      await Promise.all(linkedCharts.map(c =>
        deleteDoc(doc(db, "users", user.uid, "charts", c.id))
      ));
    } else {
      setDatasets((prev) => { const next = { ...prev }; delete next[datasetId]; return next; });
      setCharts((prev) => {
        const next = { ...prev };
        Object.values(prev).forEach((chart) => { if (chart.datasetId === datasetId) delete next[chart.id]; });
        return next;
      });
    }
  }, [isFirestoreAvailable, user, charts]);

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
    saveDataset, deleteDataset,
    saveChart, deleteChart,
    duplicateChart, generateId
  }), [charts, datasets, deleteChart, deleteDataset, duplicateChart, loading, saveChart, saveDataset]);

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
  const context = useContext(StoreContext);
  if (!context) throw new Error("useStore must be used within StoreProvider");
  return context;
}