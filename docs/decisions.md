# Key decisions and trade-offs

| Decision | Alternative | Why this way | Revisit when |
|---|---|---|---|
| Probabilistic triggers from the ECMWF 51-member ensemble | One deterministic forecast | Exposes real uncertainty; matches how anticipatory-action triggers are designed; explainable ("38 of 51") | Never for triggers; deterministic stays for display |
| Code computes, the LLM reasons | Let the LLM read raw member arrays | Testable numbers, no arithmetic hallucination, far fewer tokens | — |
| LLM outputs `chunk_id + quote` only; code fills in title/page | Let the LLM cite pages | The model cannot invent a page number; the verbatim check becomes a plain substring test | — |
| Deterministic VERIFY + LLM critic | LLM-only self-critique | Hard guarantees (grounding, numbers, facilities, phases) must not depend on a model's judgement | — |
| LangGraph state graph with explicit nodes | One big prompt / free-form agent loop | Inspectable steps, bounded retries, streamable trace | — |
| Hybrid retrieval (own BM25 + vectors, RRF) with a relevance gate | Pure vector search | Place and department names need keyword matching; the gate turns weak matches into an honest "Not found in district plan" | — |
| Local embeddings (fastembed, ONNX) with TF-IDF fallback | Paid embedding API | Free tier only; works offline; failure mode is explicit | Better Indic-language embeddings are needed |
| Claude via the Anthropic SDK, backup model behind one interface | Provider lock-in / OpenAI shims | Structured outputs, adaptive thinking, server-side refusal fallback; backup model survives model-specific outages | A second provider is required by the organisers |
| Rainfall primary, river supporting | River primary | GloFAS (~5 km) is weak on small urban channels; urban floods are rain-driven | Local river gauges (CWC) become available |
| Hysteresis counted in model runs (meta.json run time, else data fingerprint) | Count scans | Re-scanning the same 6-hourly run must not count twice | — |
| Human approval for everything; two-step publish token bound to the message text | Auto-send at ACT | Safety, accountability and trust; the token proves the officer saw exactly the text that is published | Never in this prototype |
| SSE for the trace and notifications | WebSockets | One-way streams; simpler; proxy-friendly; resumable with Last-Event-ID | Two-way collaboration is needed |
| SQLite by default, Postgres via `DATABASE_URL` | Postgres + pgvector only | Zero-setup local runs and simple free-tier deploys; vectors are few enough to score in NumPy | Thousands of plan documents |
| Single container serving API + dashboard | Separate Vercel frontend | One public URL, no CORS, fewer moving parts during judging | Independent frontend scaling |
| Replay with multi-model agreement from as-issued deterministic runs | Historical Forecast API | The Historical Forecast API stitches the first hours of each run (close to observations) and would leak the future | Archived ensemble members become available |
| Monitored set: all 38 Tamil Nadu districts + 6 watch-list districts | Every Indian district | Fits free-tier limits; depth over breadth | A paid tier or own data mirror |

## Calibration (to be filled with replay results)

Starting values from `config/hazards.yaml` (`antecedent_rain_72h_mm = 100`, `prob_margin = 0.15`, two consecutive runs) are tuned by running replay on several events and quiet periods. Record each replay here: event, lead time per level, false alarms in the quiet period, and any threshold change made. Use the real values reported by the Replay page — never round them up.

| Date | Event | WATCH lead | PREPARE lead | ACT lead | Quiet-period false alarm | Change |
|---|---|---|---|---|---|---|
| 2026-10-31 | Cyclone Fengal — Viluppuram (8 archived global models, as issued; ERA5 peak 394.7 mm/24 h ending 2024-12-01 12:00 UTC) | 48 h | **30 h** | 24 h | none (1–9 Oct 2024, observed max 11.9 mm/24 h) | none — starting values kept |

Fengal — Puducherry and Michaung — Chennai could not be replayed from the build environment (Open-Meteo daily limit on the shared IP); run them from the deployment.
