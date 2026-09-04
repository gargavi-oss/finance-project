# DocForensic AI — Nexora / NioHack 2026

> Six-agent fraud-detection pipeline for invoices and receipts.
> Upload → in under 10 seconds, fully-explained risk verdict.

This repository implements **Team Nexora (T-1008)**'s submission for the
**AI Agent for Finance** track at **NioHack 2026** (Niograph Inc, USA).
It is a working MVP of **DocForensic AI**: an open-source alternative to
Klippa / Trustpair / Staple AI that any SME or startup can deploy without
procurement budget, an IT team, or an ERP integration.

```
┌─────────────┐     ┌──────────────────────────┐
│  Invoice    │ ──▶ │  DocForensic AI Pipeline │
└─────────────┘     └──────────────────────────┘
                          │   ┌──────────┐
                          ├──▶│ Forensics │  ELA · metadata · font-consistency
                          ├──▶│ Policy   │  RAG over company expense policy
                          ├──▶│ History  │  vendor statistics
                          ├──▶│ Ring     │  cross-document fingerprint
                          ▼   └──────────┘
                     Risk score (0-100) + plain-English explanation
```

---

## What's in the box

| Layer | Tech |
|---|---|
| Frontend | React 18 + TypeScript + Vite + Tailwind CSS |
| Backend | Python 3.11+ · FastAPI · LangGraph orchestration |
| OCR | Tesseract 5 (with a deterministic demo fallback for bundled samples) |
| Forensics | OpenCV + Pillow + imagehash (ELA, EXIF, font variance, pHash) |
| RAG | scikit-learn TF-IDF over the company expense policy |
| Database | SQLite (via SQLAlchemy 2 async) |
| Live trace | Server-Sent Events |

---

## Quickstart

### 1. Prerequisites

* **Python 3.11+** (3.12 recommended)
* **Node.js 18+** (20+ recommended)
* (Optional) **Tesseract 5** — needed only if you upload real receipt scans.
  macOS: `brew install tesseract` · Ubuntu: `sudo apt install tesseract-ocr`
* (Optional) **OpenAI API key** — leave empty to run in deterministic demo mode.

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Generate sample invoices (4 PNGs in data/invoices/)
python -m scripts.generate_samples --out ./data/invoices

# Run the server
cp .env.example .env            # then edit if needed
uvicorn app.main:app --reload --port 8000
```

The first run will:

1. create `./data/docforensic.db` (SQLite)
2. load `./data/policy/acme_expense_policy.md` into the RAG index
3. expose the API on `http://localhost:8000`

Verify with:

```bash
curl http://localhost:8000/api/health
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev                     # opens http://localhost:5173
```

The Vite dev server proxies `/api/*` and `/data/*` to the backend on
port 8000 — no CORS or extra config required.

### 4. Try the demo

1. Open `http://localhost:5173`
2. From **Live Verification**, drag any of the bundled samples from
   `backend/data/invoices/`:
   * `INV-2026-00181.png` — clean invoice, should pass
   * `INV-2026-04182.png` — tampered total, ELA + Forensics should fire
   * `ring_alpha_supplies.png` & `ring_beta_office.png` — submit both;
     the Ring-Detection agent should link them
3. Watch the six-agent pipeline run live in the right panel.
4. Click **Run verification** to open the **Verdict Report**.
5. Hit **Approve / Escalate / Reject** to log a decision to the audit trail.

---

## Project structure

```
docforensic-ai/
├── backend/
│   ├── app/
│   │   ├── api/                  FastAPI routers (documents, system)
│   │   ├── agents/               The six agents
│   │   │   ├── extraction.py     OCR pulls structured fields
│   │   │   ├── forensics.py      ELA + metadata + font heuristics
│   │   │   ├── policy.py         RAG over company expense policy
│   │   │   ├── history.py        Vendor statistical anomalies
│   │   │   ├── ring.py           Cross-document fingerprint matching
│   │   │   └── verdict.py        Synthesis + risk score
│   │   ├── services/             Low-level utilities
│   │   │   ├── ocr.py            Tesseract + embedded-payload fallback
│   │   │   ├── forensics.py      ELA, EXIF, font inconsistency
│   │   │   ├── fingerprint.py    pHash index (ring detection)
│   │   │   ├── policy_rag.py     TF-IDF RAG over the policy markdown
│   │   │   ├── vendor_history.py Statistics against past submissions
│   │   │   └── llm.py            OpenAI/Anthropic facade + demo fallback
│   │   ├── schemas/              Pydantic models (the API contract)
│   │   ├── db/                   SQLAlchemy 2 async
│   │   ├── orchestration/        LangGraph pipeline + fallback
│   │   ├── config.py             Settings (env-driven)
│   │   └── main.py               FastAPI app
│   ├── data/
│   │   ├── policy/               ACME Corp expense policy markdown
│   │   ├── invoices/             Demo invoices (auto-generated)
│   │   ├── uploads/              User-submitted uploads
│   │   └── overlays/             ELA heatmaps (one per submission)
│   ├── scripts/
│   │   └── generate_samples.py   Regenerate demo invoices
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/Shell.tsx  Sidebar + topbar layout
│   │   ├── pages/
│   │   │   ├── LiveVerification.tsx   upload + agent trace
│   │   │   ├── VerdictReport.tsx      risk score + findings + decisions
│   │   │   ├── AuditLog.tsx           decisions & submissions
│   │   │   └── RingDetection.tsx      pHash cluster explorer
│   │   ├── lib/api.ts            Typed fetch wrapper
│   │   ├── lib/format.ts         UI helpers
│   │   └── styles/index.css      Tailwind layer + design tokens
│   ├── index.html
│   ├── tailwind.config.js
│   ├── vite.config.ts
│   └── package.json
├── README.md
└── .gitignore
```

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| `POST`  | `/api/documents`                       | Upload a file, kick off the pipeline |
| `GET`   | `/api/documents/{id}/stream`           | SSE stream of agent progress |
| `GET`   | `/api/documents/{id}`                  | Document metadata |
| `GET`   | `/api/documents/{id}/payload`          | Full per-agent results (pollable) |
| `POST`  | `/api/documents/{id}/decision`         | Approve / escalate / reject |
| `GET`   | `/api/documents`                       | Recent submissions |
| `GET`   | `/api/audit`                           | Decision history |
| `GET`   | `/api/rings`                           | All indexed documents (for Ring page) |
| `GET`   | `/api/health`                          | LLM provider, Tesseract availability, version |

---

## How the six agents cooperate

The pipeline mirrors **Section 6 — Methodology** of the proposal:

1. **Extraction** runs first — everything downstream depends on its output.
2. **Forensics**, **Policy**, and **History** can run in parallel.
3. **Ring-Detection** runs once forensics has computed the perceptual hash.
4. **Verdict** fuses everything into a 0-100 risk score with citations.

For each agent the API publishes:

* the per-agent result (`/api/documents/{id}/payload`)
* the live status badge (`queued → running → passed / review / high / error`)
* the structured evidence the UI renders (flagged ELA region, cited policy
  clause, vendor history stats, ring-match table)

The Verdict agent's headline summary is LLM-generated when `OPENAI_API_KEY`
or `ANTHROPIC_API_KEY` is set; otherwise a deterministic template produces
a faithful 3-5 sentence synthesis.

---

## Tech-stack alignment with the proposal

| Proposal line | Implementation |
|---|---|
| Frontend · React + TypeScript, Tailwind CSS | `frontend/src/` (Vite + TS + Tailwind 3) |
| Backend · Node.js / Express, Python + FastAPI | **`Python + FastAPI`** chosen — best fit for CV / OCR / agents |
| AI & ML · Tesseract / Cloud Vision OCR, OpenCV + PIL (ELA), Claude / GPT API | Tesseract primary, embedded-payload demo OCR fallback; OpenCV + PIL ELA; OpenAI / Anthropic facade |
| Database · PostgreSQL + Prisma + pgvector, MongoDB | **SQLite** for the MVP — schema is SQLAlchemy 2 async, swap to Postgres with one URL |
| APIs & Automation · Cloudinary, LangChain / LangGraph | LangGraph orchestration; static `/data` mount for images + ELA overlays |
| Data Sources · Self-built tamper/genuine dataset, public expense-policy PDFs | `scripts/generate_samples.py` produces a tampered + ring-fraud dataset; `data/policy/` holds an example expense policy |

---

## Senior-developer notes

A few decisions worth pointing out for the demo:

* **Embedded demo OCR.** The Tesseract binary is a real-world dependency
  that's often missing on minimal dev machines. To make the demo work
  out-of-the-box we serialise ground-truth fields into the EXIF UserComment
  of the generated sample images. The OCR service checks this first and
  only falls back to Tesseract when it's missing. Real production uploads
  go straight to Tesseract.
* **LangGraph with a fallback.** `orchestration/graph.py` imports LangGraph
  lazily. If LangGraph can't be imported (eg. the user forgot to `pip
  install`) the same observable event stream is produced by a tiny
  sequential runner. The SSE protocol and the UI contract are identical
  in both cases.
* **Per-document state.** Each upload gets a fresh `PipelineState`; results
  are mirrored into SQLite after each agent finishes so reloading the
  Verdict Report page after the SSE closes still shows everything.
* **Ring detection is in-memory on top of SQLite.** `FingerprintIndex`
  bootstraps from the documents table once and appends new entries as
  they're processed. For >100k docs you'd switch to a real ANN index
  (FAISS / pgvector) — the call signature is one-line.
* **Frontend is dark-theme by default with orange/red accent** to match the
  designed UI from the proposal. No external icon library — every glyph is
  hand-drawn SVG to keep the bundle small.

---

## License

This project was built for NioHack 2026. Treat it as MIT-licensed for the
purposes of the hackathon unless your team agrees otherwise.# finance-project
