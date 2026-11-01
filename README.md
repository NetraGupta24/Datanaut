# DATANAUT

A React + Vite app for importing datasets, exploring fields, and building chart-driven dashboards. Signed-in users keep their datasets and charts in Firebase; full datasets are stored and analysed by a small Python data service, and a Node backend streams live data (MQTT, HTTP, serial, SQL) to the dynamic dashboard.

## Features
- Drag & drop import for CSV, XLSX, and JSON files with automatic schema/type inference
- Google Sheets support via public CSV share links (auto-transforms standard share URLs)
- Preview tables with sticky headers, type badges, and the first 50 rows of data
- Chart builder with live Recharts preview for Line, Bar, Area, Scatter, Pie, Donut, and Radar charts
- Aggregation controls (none, sum, avg, min, max) plus Top N filtering
- Edit/duplicate/delete charts from the dashboard; export any chart as PNG using `html2canvas`
- Firebase Auth (email/password and Google) with guarded routes; a demo mode that needs no Firebase
- Data assistant: ask questions about a dataset in plain English; pandas does every calculation and each answer shows how it was calculated. Ask for a chart and it can be added to the dashboard
- Live dashboard with anomaly alerts: readings far from their recent values are flagged and drawn in red

## Project Structure
```
.
├── public/samples/         # Seed datasets
├── src/
│   ├── pages/              # Login, Data, Visualize, Dashboard, dynamic (live) pages, 404
│   ├── providers/          # Auth, Store and Theme contexts
│   ├── ui/                 # ChartRenderer, ChatBot, DataPreviewTable, ...
│   ├── utils/              # Data parsing, chart data helpers, data service client
│   ├── backend/            # Live data backend (Express + WebSocket, port 8085)
│   ├── App.jsx             # Routes + layout
│   └── main.jsx            # App bootstrap
├── chatbot-service/        # Data service (FastAPI, port 8000): dataset storage, profiling, chat
├── firestore.rules         # Firestore security rules
└── tailwind.config.js      # Tailwind configuration
```

## Getting Started

DATANAUT runs as three services:

| Service | Folder | Port | Needed for |
|---|---|---|---|
| Frontend (React + Vite) | repo root | 5173 | everything |
| Data service (FastAPI) | `chatbot-service/` | 8000 | uploading datasets when signed in, the data assistant |
| Live backend (Express + WebSocket) | `src/backend/` | 8085 | the Dynamic Data / Visualize / Dashboard pages |

Prerequisites: Node.js 18+, Python 3.11+, and a Firebase project with Authentication (Email/Password, optionally Google) and Firestore enabled. See [FIREBASE_SETUP.md](FIREBASE_SETUP.md) for the Firebase console steps.

> **Secrets:** `.env` files and the service account JSON are gitignored; never commit them. The Groq API key lives only in `chatbot-service/.env`. Nothing secret goes in the frontend `.env`: every `VITE_*` value is visible in the browser.

### 1. Frontend (port 5173)
```bash
npm install
cp .env.example .env        # PowerShell: Copy-Item .env.example .env
npm run dev
```

Frontend `.env`:

| Variable | Description |
|---|---|
| `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_STORAGE_BUCKET`, `VITE_FIREBASE_MESSAGING_SENDER_ID`, `VITE_FIREBASE_APP_ID` | Firebase web app config (Project settings > Your apps > Web app). These are public identifiers; `firestore.rules` is what protects the data |
| `VITE_API_URL` | Data service URL, default `http://localhost:8000` |
| `VITE_DEMO_MODE` | `true` runs without Firebase: any email/password logs in and data stays in `localStorage`. The data assistant needs a real account, so it is off in demo mode |

Deploy the Firestore rules once per project (each user can only read and write `users/{their uid}/**`):
```bash
npx firebase-tools login
npx firebase-tools deploy --only firestore:rules --project your-project-id
```

Visit `http://localhost:5173` and sign in. Sample datasets and charts are added to a new account on first sign-in.

### 2. Data service (port 8000)
```bash
cd chatbot-service
python -m venv .venv
.venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # PowerShell: Copy-Item .env.example .env
uvicorn app.main:app --port 8000
```

The service checks the Firebase ID token on every request, stores full datasets as Parquet files under `chatbot-service/data/` (gitignored), and writes each dataset's metadata and profile to Firestore. Health check: `http://localhost:8000/health`.

`chatbot-service/.env`:

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | (required) | Path to the Firebase service account JSON (Project settings > Service accounts). Keep it outside the repo, e.g. `E:\secrets\datanaut-sa.json` |
| `FIREBASE_PROJECT_ID` | from the service account | Firebase project id |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins allowed to call the API |
| `DATA_DIR` | `data` | Where Parquet files are stored (relative to `chatbot-service/`) |
| `MAX_UPLOAD_MB` | `50` | Largest accepted upload |
| `DATAFRAME_CACHE_SIZE` | `10` | Datasets kept in memory |
| `GROQ_API_KEY` | (required for chat) | Groq API key; the chat endpoint returns 503 without it |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model with tool calling |
| `GROQ_TIMEOUT_S` | `30` | Timeout for one Groq request |
| `CHAT_RATE_LIMIT` | `20/minute` | Chat requests allowed per user |

Run the tests with `python -m pytest` (from `chatbot-service/`, venv active).

### 3. Live backend (port 8085)
```bash
cd src/backend
npm install
npm start
```

The live pages expect the backend at `http://localhost:8085`. It has no `.env` file; set these in the shell before `npm start` if you need them:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8085` | Port to listen on (the frontend expects 8085) |
| `ANOMALY_WINDOW` | `50` | Recent readings per connection and field used for anomaly detection |
| `ANOMALY_Z_THRESHOLD` | `3` | A reading is an anomaly when its z-score against that window is above this |

For example, `$env:ANOMALY_Z_THRESHOLD = "2.5"; npm start` in PowerShell, or `ANOMALY_Z_THRESHOLD=2.5 npm start` in bash.

**Demo without hardware:** with the backend running, start the sensor simulator in another terminal:
```bash
cd src/backend
npm run simulate
```
It creates a "Sensor simulator" HTTP connection and a live temperature chart (if they don't exist yet), then posts a reading to `/api/sensor-data` every second with an occasional spike. Open **Dynamic Dashboard** to see the spikes drawn in red and listed under **Anomaly alerts**. Options: `npm run simulate -- --interval 500 --spike-chance 0.1 --count 200 --device sim-02 --skip-setup`.

Run the backend tests with `npm test` (from `src/backend/`).

## Building for Production
```bash
npm run build
npm run preview
```

## Authentication
- Sign in with email/password or Google through Firebase Auth; each user's datasets and charts live under `users/{uid}` in Firestore
- With `VITE_DEMO_MODE=true`, any email and password sign you in and nothing leaves the browser

## Resetting Local Storage (demo mode)
Clear the following keys in your browser console to reset the app state:
```js
['datasets', 'charts', 'seeded', 'chat_messages', 'chat_dataset_id'].forEach((key) => localStorage.removeItem(key));
```
Reload the page and sign in again; the sample datasets and charts are added back.
