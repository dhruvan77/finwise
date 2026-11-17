# FinWise AI - Frontend

Static frontend (HTML + CSS + vanilla JS). No build step, no npm install.

## Structure
```
index.html          page shell
css/style.css       all styles + animations
js/config.js        API base URL (edit here)
js/app.js           auth, API client, pages, charts, import
assets/favicon.svg
sample-data/finwise_sample_transactions.csv   raw data to upload
vercel.json
```

## Run locally
```
cd finwise-frontend
python3 -m http.server 3000
```
Open http://localhost:3000 (add `http://localhost:3000` to `CORS_ORIGINS` on Render).

## Use
1. Register (or sign in).
2. Import Data -> drop `sample-data/finwise_sample_transactions.csv` (or "Load sample data").
3. Explore Overview, Digital Twin, Simulator, Stress Testing, Alerts, AI Coach.

## Deploy to Vercel
1. Push this folder to GitHub (or run `npx vercel` inside it).
2. Import the repo in Vercel. Framework preset: **Other**. Build command: empty. Output directory: `.`
3. Copy the Vercel URL and add it to `CORS_ORIGINS` in Render (comma-separated), then redeploy the backend.

## Backend requirements
- Supabase: Authentication -> Email -> turn off "Confirm email" for demos.
- Render: `GROQ_API_KEY` set (check `/api/v1/health` shows `"groq": true`).
- Tokens expire (~1h) and the backend has no refresh endpoint; the app signs you out with a notice.
