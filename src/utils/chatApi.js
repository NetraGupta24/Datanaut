// Builds a rich, structured summary from datasets and charts
export function buildRichSummary(datasets, charts) {
  if (!datasets || Object.keys(datasets).length === 0) {
    return 'No datasets loaded yet.';
  }

  const lines = [];

  // Dataset analysis
  Object.values(datasets).forEach((ds) => {
    const cols = ds?.schema?.headers || [];
    const rows = ds?.data || ds?.rowsPreview || [];
    const rowCount = ds?.originalRowCount ?? rows.length;

    lines.push(`📊 Dataset: "${ds.name}"`);
    lines.push(`   Rows: ${rowCount.toLocaleString()}, Columns: ${cols.length}`);
    lines.push(`   Columns: ${cols.join(', ')}`);

    // Numeric column stats
    const numericStats = [];
    cols.forEach((col) => {
      const vals = rows
        .map((r) => parseFloat(r[col]))
        .filter((v) => !isNaN(v));
      if (vals.length > 0) {
        const sum = vals.reduce((a, b) => a + b, 0);
        const mean = sum / vals.length;
        const min = Math.min(...vals);
        const max = Math.max(...vals);
        numericStats.push(
          `   "${col}": avg=${mean.toFixed(2)}, min=${min}, max=${max}, sum=${sum.toFixed(2)}`
        );
      }
    });

    if (numericStats.length > 0) {
      lines.push(`   Numeric stats:`);
      numericStats.slice(0, 5).forEach((s) => lines.push(s));
    }

    // Categorical column insights (top values)
    cols.forEach((col) => {
      const vals = rows.map((r) => r[col]).filter((v) => v !== undefined && v !== null && v !== '');
      const isNumeric = vals.every((v) => !isNaN(parseFloat(v)));
      if (!isNumeric && vals.length > 0) {
        const freq = {};
        vals.forEach((v) => { freq[String(v)] = (freq[String(v)] || 0) + 1; });
        const top = Object.entries(freq)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([k, v]) => `${k}(${v})`)
          .join(', ');
        lines.push(`   Top "${col}" values: ${top}`);
      }
    });

    lines.push('');
  });

  // Charts summary
  if (charts && Object.keys(charts).length > 0) {
    lines.push(`📈 Saved charts (${Object.keys(charts).length}):`);
    Object.values(charts).forEach((ch) => {
      lines.push(`   - "${ch.title}" (${ch.chartType})`);
    });
  }

  return lines.join('\n');
}

export async function sendChatMessage({
  question = '',
  summary = '',
  datasets = null,
  charts = null,
  useMock = false,
  timeoutMs = 15000,
} = {}) {
  if (useMock) {
    return mockChatResponse(question, summary);
  }

  const key = typeof import.meta !== 'undefined' &&
              import.meta.env &&
              import.meta.env.VITE_GROQ_KEY;

  if (!key) throw new Error('No Groq API key found. Add VITE_GROQ_KEY to your .env file.');

  // Build rich summary from actual data if available
  const richSummary = (datasets && Object.keys(datasets).length > 0)
    ? buildRichSummary(datasets, charts)
    : summary || 'No dataset loaded yet.';

  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${key}`,
      },
      body: JSON.stringify({
        model: 'llama-3.3-70b-versatile',
        messages: [
          {
            role: 'system',
            content: `You are a smart data analysis assistant for a dashboard called DATANAUT.
You have access to the user's actual dataset statistics and chart information.
When answering:
- Give specific numbers and insights from the data
- Suggest useful visualizations based on the columns available
- Point out interesting patterns or anomalies
- Be concise but insightful
- Use bullet points for clarity when listing multiple insights`
          },
          {
            role: 'user',
            content: `Here is my current data:\n\n${richSummary}\n\nMy question: ${question}`
          }
        ],
        temperature: 0.3,
        max_tokens: 700,
      }),
      signal: controller.signal,
    });

    clearTimeout(id);

    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`Groq error ${res.status}: ${txt}`);
    }

    const json = await res.json();
    const text = json?.choices?.[0]?.message?.content || 'No response from Groq.';
    return { text };

  } catch (err) {
    clearTimeout(id);
    throw err;
  }
}

function mockChatResponse(question, summary) {
  return new Promise((resolve) => {
    setTimeout(() => {
      let response = '';
      if (question.toLowerCase().includes('summary')) {
        response = `Here's a summary of your data:\n${summary}`;
      } else if (question.toLowerCase().includes('chart')) {
        response = 'Your charts show relationships between variables. Try adding more visualizations!';
      } else if (question.toLowerCase().includes('help')) {
        response = 'I can help you analyze data, explain charts, and suggest visualizations. Just ask!';
      } else {
        response = "I've analyzed your data and found some interesting patterns.";
      }
      resolve({ text: response });
    }, 800);
  });
}

export default sendChatMessage;