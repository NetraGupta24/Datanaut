import React, { useEffect, useMemo, useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { MessageSquare, Send } from 'lucide-react';
import { sendChatMessage, formatProfile, MAX_HISTORY_MESSAGES } from '../utils/chatApi';
import { useStore } from '../providers/StoreContext.jsx';
import DatasetChart from './DatasetChart.jsx';
import { defaultPalette } from '../utils/colors.js';

const MESSAGES_KEY = 'chat_messages';
const DATASET_KEY = 'chat_dataset_id';
const MAX_STORED_MESSAGES = 100;
const MAX_MESSAGE_CHARS = 2000;

function formatDate(ts) {
  const d = new Date(ts || Date.now());
  return d.toLocaleString();
}

const readStored = (key, fallback) => {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
};

// Datasets saved by the data service have full rows and a profile the assistant can use
const canChat = (dataset) => Boolean(dataset?.storagePath && dataset?.profile);

function formatValue(value) {
  if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (value === null || value === undefined) return '–';
  return String(value);
}

function describeCall({ tool, args = {} }) {
  const shown = Object.entries(args)
    .filter(([key, value]) => key !== 'filters' && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}=${value}`);
  const where = (args.filters || []).map((f) => {
    const value = Array.isArray(f.value) ? f.value.join(f.op === 'between' ? ' – ' : ', ') : f.value;
    return `${f.column} ${f.op} ${value}`;
  });
  return `${tool}(${shown.join(', ')})${where.length ? ` where ${where.join(' and ')}` : ''}`;
}

// Answers may use **bold**; render that and leave everything else as plain text
function MessageText({ text }) {
  const parts = String(text || '').split(/\*\*(.+?)\*\*/g);
  return (
    <div className="whitespace-pre-wrap text-sm">
      {parts.map((part, i) => (i % 2 === 1 ? <strong key={i}>{part}</strong> : <React.Fragment key={i}>{part}</React.Fragment>))}
    </div>
  );
}

function Evidence({ evidence }) {
  if (!evidence?.length) return null;
  return (
    <details className="mt-2 rounded-md border border-slate-200 bg-white/70 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200">
      <summary className="cursor-pointer select-none px-2 py-1 text-xs font-medium text-indigo-600 dark:text-indigo-300">
        How this was calculated ({evidence.length} {evidence.length === 1 ? 'step' : 'steps'})
      </summary>
      <div className="space-y-3 px-2 pb-2">
        {evidence.map((item, i) => (
          <div key={i}>
            <div className="mb-1 break-words font-mono text-[11px] text-slate-500 dark:text-slate-400">{describeCall(item)}</div>
            <div className="max-h-48 overflow-auto rounded border border-slate-200 dark:border-slate-700">
              <table className="w-full text-left text-[11px]">
                <thead className="sticky top-0 bg-slate-100 dark:bg-slate-800">
                  <tr>
                    {item.result_table.columns.map((col) => (
                      <th key={col} className="px-2 py-1 font-semibold">{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {item.result_table.rows.map((row, r) => (
                    <tr key={r} className="odd:bg-white even:bg-slate-50 dark:odd:bg-slate-900 dark:even:bg-slate-800/60">
                      {row.map((cell, c) => (
                        <td key={c} className={`px-2 py-0.5 ${typeof cell === 'number' ? 'text-right tabular-nums' : ''}`}>
                          {formatValue(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {item.result_table.note ? (
              <div className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">{item.result_table.note}</div>
            ) : null}
          </div>
        ))}
      </div>
    </details>
  );
}

const CHART_TYPES = ['line', 'bar', 'area', 'scatter', 'pie', 'donut', 'radar'];
const MAPPING_FIELDS = ['xField', 'yField', 'categoryField', 'valueField', 'angleField', 'radiusField'];

// The server checks charts when it builds them; check again in case the dataset changed since
function chartProblem(chart, dataset) {
  if (!dataset) return 'This dataset no longer exists.';
  if (!CHART_TYPES.includes(chart?.chartType)) return "This chart type isn't supported.";
  const headers = dataset.schema?.headers || [];
  const mappings = chart.mappings || {};
  const fields = [...MAPPING_FIELDS.map((key) => mappings[key]), ...(mappings.yFields || [])].filter(Boolean);
  const missing = fields.filter((field) => !headers.includes(field));
  return missing.length ? `"${dataset.name}" no longer has ${missing.join(', ')}.` : null;
}

function ChartPreview({ message, dataset, onSave }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const problem = chartProblem(message.chart, dataset);

  async function add() {
    setSaving(true);
    setError('');
    try {
      await onSave(message);
    } catch (err) {
      console.error(err);
      setError(`Couldn't add the chart: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-white/70 p-2 dark:border-slate-700 dark:bg-slate-900/60">
      {problem ? (
        <div className="text-xs text-rose-600 dark:text-rose-300">{problem}</div>
      ) : (
        <DatasetChart chart={message.chart} dataset={dataset} compact />
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {message.savedChartId ? (
          <Link to="/dashboard" className="text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400">
            Added to dashboard · Open dashboard
          </Link>
        ) : (
          <button
            type="button"
            onClick={add}
            disabled={saving || Boolean(problem)}
            className="rounded-full bg-gradient-to-r from-indigo-600 to-purple-600 px-3 py-1 text-xs font-semibold text-white hover:from-indigo-700 hover:to-purple-700 disabled:opacity-50"
          >
            {saving ? 'Adding…' : 'Add to dashboard'}
          </button>
        )}
        {error ? <span className="text-[11px] text-rose-600 dark:text-rose-300">{error}</span> : null}
      </div>
    </div>
  );
}

export default function ChatBot() {
  // Datasets come from the store so logged-in users see their Firestore data
  const { datasets, usesServerStorage, saveChart, generateId } = useStore();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState(() => readStored(MESSAGES_KEY, []));
  const [loading, setLoading] = useState(false);
  const [datasetId, setDatasetId] = useState(() => readStored(DATASET_KEY, ''));
  const listRef = useRef(null);

  const datasetOptions = useMemo(
    () => Object.values(datasets).sort((a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0)),
    [datasets]
  );
  const selected = datasets[datasetId];
  const visibleMessages = useMemo(() => messages.filter((m) => m.datasetId === datasetId), [messages, datasetId]);

  // Default to the newest chat-ready dataset, or replace one that was deleted
  useEffect(() => {
    if (!datasetOptions.length || datasets[datasetId]) return;
    const fallback = (usesServerStorage && datasetOptions.find(canChat)) || datasetOptions[0];
    setDatasetId(fallback.id);
  }, [datasetId, datasets, datasetOptions, usesServerStorage]);

  useEffect(() => {
    try {
      if (datasetId) localStorage.setItem(DATASET_KEY, JSON.stringify(datasetId));
    } catch (e) {
      // ignore
    }
  }, [datasetId]);

  useEffect(() => {
    try {
      localStorage.setItem(MESSAGES_KEY, JSON.stringify(messages.slice(-MAX_STORED_MESSAGES)));
    } catch (e) {
      // ignore
    }
    // scroll to bottom
    setTimeout(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    }, 50);
  }, [messages, datasetId]);

  // The old settings panel stored an OpenAI key in plain text; clear any leftover copy
  useEffect(() => {
    try {
      localStorage.removeItem('openai_api_key');
    } catch (e) {
      // ignore
    }
  }, []);

  async function handleSend(text) {
    const trimmed = (text || '').trim();
    if (!trimmed || loading) return;
    setInput('');

    const add = (msg) =>
      setMessages((m) => [...m, { id: Date.now() + Math.random(), ts: Date.now(), datasetId, ...msg }]);

    // History for this dataset only, without /summary output or error replies
    const history = messages
      .filter((m) => m.datasetId === datasetId && !m.local && !m.error)
      .slice(-MAX_HISTORY_MESSAGES)
      .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.text }));

    if (trimmed === '/summary') {
      add({ role: 'user', text: trimmed, local: true });
      add({ role: 'bot', text: selected ? formatProfile(selected) : 'Pick a dataset first.', local: true });
      return;
    }

    add({ role: 'user', text: trimmed });
    const problem = !selected
      ? 'Pick a dataset to ask about.'
      : !usesServerStorage
        ? "The assistant needs a signed-in account, so it isn't available in demo mode. /summary still works."
        : !canChat(selected)
          ? `"${selected.name}" was saved before the data service existed. Re-upload it on the Data page to chat about it.`
          : null;
    if (problem) {
      add({ role: 'bot', text: problem, error: true });
      return;
    }

    setLoading(true);
    try {
      const resp = await sendChatMessage({ datasetId, message: trimmed, history });
      add({ role: 'bot', text: resp.answer, evidence: resp.evidence, chart: resp.chart || null });
    } catch (err) {
      console.error(err);
      add({ role: 'bot', text: err.message || 'Something went wrong. Please try again.', error: true });
    } finally {
      setLoading(false);
    }
  }

  // Save the suggested chart with the same fields the Visualize page writes
  async function saveSuggestedChart(message) {
    const now = new Date().toISOString();
    const id = generateId();
    const { chart } = message;
    await saveChart({
      ...chart,
      id,
      options: { ...chart.options, seriesColors: {}, palette: defaultPalette.slice() },
      createdAt: now,
      updatedAt: now
    });
    setMessages((all) => all.map((m) => (m.id === message.id ? { ...m, savedChartId: id } : m)));
  }

  function exportChat() {
    const text = visibleMessages.map((m) => `[${formatDate(m.ts)}] ${m.role.toUpperCase()}: ${m.text}`).join('\n\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `DATANAUT_chat_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function clearChat() {
    setMessages((m) => m.filter((msg) => msg.datasetId !== datasetId));
  }

  return (
    <>
      <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end space-y-2">
        <button
          onClick={() => setOpen((o) => !o)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white p-4 rounded-full shadow-lg flex items-center justify-center"
          title="Toggle chat"
          style={{ width: '60px', height: '60px' }}
        >
          <MessageSquare className="w-8 h-8" />
        </button>
      </div>

      <div
        className={`fixed bottom-20 right-6 z-40 transition-transform duration-200 ${open ? 'translate-y-0' : 'translate-y-6 opacity-0 pointer-events-none'}`}
        style={{ width: 450, maxWidth: 'calc(100vw - 3rem)' }}
      >
        <div className="bg-white bg-opacity-70 dark:bg-slate-900 dark:bg-opacity-70 backdrop-blur-md border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl flex flex-col overflow-hidden">
          <div className="px-4 py-3 flex items-center justify-between border-b border-slate-100 dark:border-slate-800 bg-gradient-to-r from-indigo-600 to-purple-600 text-white">
            <div className="flex items-center space-x-2">
              <MessageSquare className="w-5 h-5 text-white" />
              <div>
                <div className="text-sm font-semibold">Data Assistant</div>
                <div className="text-xs text-indigo-100">Answers are calculated from your data</div>
              </div>
            </div>
            <div className="flex items-center space-x-2">
              <button onClick={exportChat} className="text-xs px-2 py-1 rounded bg-white/20 hover:bg-white/30 text-white">
                Export
              </button>
              <button onClick={clearChat} className="text-xs px-2 py-1 rounded bg-red-500 text-white hover:bg-red-600">
                Clear
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 border-b border-slate-100 bg-white/60 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/60">
            <label htmlFor="chat-dataset" className="text-xs font-medium text-slate-500 dark:text-slate-400">
              Dataset
            </label>
            <select
              id="chat-dataset"
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              className="min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              {datasetOptions.length === 0 ? <option value="">No datasets yet</option> : null}
              {datasetOptions.map((ds) => (
                <option key={ds.id} value={ds.id} disabled={usesServerStorage && !canChat(ds)}>
                  {ds.name || 'Untitled dataset'}
                  {usesServerStorage && !canChat(ds) ? ' (re-upload to chat)' : ''}
                </option>
              ))}
            </select>
          </div>
          {!usesServerStorage ? (
            <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
              Demo mode: sign in with a real account to chat. /summary still works.
            </div>
          ) : null}

          <div ref={listRef} className="p-3 h-96 max-h-[calc(100vh-18rem)] overflow-auto bg-slate-50 dark:bg-slate-950">
            {visibleMessages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-center p-4">
                <div className="w-16 h-16 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center mb-3">
                  <MessageSquare className="w-8 h-8 text-indigo-600 dark:text-indigo-400" />
                </div>
                <div className="text-sm font-medium text-slate-700 dark:text-slate-300 mb-1">Your Data Assistant</div>
                <div className="text-xs text-slate-500 dark:text-slate-400 mb-3">
                  Ask about {selected ? `"${selected.name}"` : 'your dataset'}: totals, top values, trends, comparisons, outliers,
                  or ask for a chart.
                </div>
                <div className="text-xs text-indigo-600 dark:text-indigo-400">Type /summary for a quick dataset overview</div>
              </div>
            )}
            {visibleMessages.map((m) => (
              <div key={m.id} className={`mb-3 flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`${
                    m.role === 'user'
                      ? 'bg-gradient-to-r from-indigo-600 to-purple-600 text-white'
                      : m.error
                        ? 'bg-rose-50 dark:bg-rose-900/30 text-rose-700 dark:text-rose-200 border border-rose-200 dark:border-rose-800'
                        : 'bg-white bg-opacity-60 dark:bg-slate-800 dark:bg-opacity-60 backdrop-blur-md text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700'
                  } p-3 rounded-lg ${m.chart ? 'w-full' : 'max-w-[85%]'} shadow-md`}
                >
                  <div className="text-xs opacity-70 mb-1">{m.role === 'user' ? 'You' : 'Assistant'} • {formatDate(m.ts)}</div>
                  <MessageText text={m.text} />
                  {m.chart ? <ChartPreview message={m} dataset={datasets[m.datasetId]} onSave={saveSuggestedChart} /> : null}
                  <Evidence evidence={m.evidence} />
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start mb-3">
                <div className="bg-white bg-opacity-60 dark:bg-slate-800 dark:bg-opacity-60 backdrop-blur-md border border-slate-200 dark:border-slate-700 p-3 rounded-lg shadow-md">
                  <div className="flex items-center space-x-2">
                    <div className="w-2 h-2 rounded-full bg-indigo-600 animate-pulse"></div>
                    <div className="w-2 h-2 rounded-full bg-indigo-600 animate-pulse delay-150"></div>
                    <div className="w-2 h-2 rounded-full bg-indigo-600 animate-pulse delay-300"></div>
                    <span className="text-sm text-slate-500 ml-1">Calculating...</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="px-3 py-3 border-t border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="flex items-center space-x-2">
              <input
                value={input}
                maxLength={MAX_MESSAGE_CHARS}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend(input);
                  }
                }}
                placeholder="Ask a question or type /summary"
                className="flex-1 px-3 py-2.5 rounded-full border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all"
              />
              <button
                onClick={() => handleSend(input)}
                disabled={loading || !input.trim()}
                className="p-2.5 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white rounded-full disabled:opacity-50 transition-all"
                title="Send"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="mt-2 text-xs text-slate-400 px-3">Tip: Use <code className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded">/summary</code> for a quick dataset overview.</div>
          </div>
        </div>
      </div>
    </>
  );
}
