# Architecture walkthrough

A guide any team member can use to walk a judge through T-Minus (golden rule 5).

## 1. One scan, end to end

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant SC as scanner.py
    participant OM as Open-Meteo (Ensemble / Forecast / Flood)
    participant SA as SACHET
    participant DB as Database
    participant TE as triggers/*
    participant AG as agent
    S->>SC: run_scan()
    par fetch in parallel
        SC->>OM: ensemble (10 districts per call, ECMWF → GFS fallback)
        SC->>OM: past 72 h rain
        SC->>OM: GloFAS river forecast
        SC->>SA: CAP feed (+ linked CAP documents)
    end
    SC->>DB: source_fetches (raw payload, latency, status)
    SC->>SC: hazards/rain.py → probabilities, t*, onset (pure maths)
    SC->>TE: escalation.resolve_level (floor, +1 cap, disagreement)
    SC->>DB: assessments (fetch_ids → raw payload)
    SC->>TE: lifecycle.step (dedupe, hysteresis by model run)
    SC->>DB: triggers (history)
    SC-->>AG: trigger opened/escalated ≥ PREPARE → start run
```

Files: `app/scanner.py`, `app/sources/*`, `app/hazards/*`, `app/triggers/*`.

## 2. The 51-futures maths (`app/hazards/ensemble_math.py`)

* `rolling_accumulation(members, 24)` — prefix sums per member; a 24-h total is NaN if any hour is missing (missing is never zero).
* `exceedance(acc, times, X, now, W)` — share of *valid* members whose max 24-h total in `[now, now+W]` reaches X. Returns counts, so the UI can say "38 of 51".
* `lead_time(...)` — `t*` = hour where the share of members ≥ X is highest (earliest on ties); onset = first hour with ≥ 10% of members ≥ X; T-minus = t* − now.

## 3. Level resolution and lifecycle (`app/triggers/`)

```mermaid
stateDiagram-v2
    [*] --> Quiet
    Quiet --> WATCH: rule met
    Quiet --> PREPARE: rule met or orange alert
    Quiet --> ACT: rule met or red alert
    WATCH --> PREPARE: rule met or escalation
    PREPARE --> ACT: rule met or escalation
    WATCH --> Quiet: below entry − margin for 2 new model runs
    PREPARE --> StandDownRecommended: below entry − margin for 2 new model runs
    ACT --> StandDownRecommended: below entry − margin for 2 new model runs
    StandDownRecommended --> PREPARE: risk rises again
    StandDownRecommended --> Closed: officer confirms (CAP Cancel)
    Closed --> [*]
```

* `levels.evaluate_rain_rules(p, cfg, margin)` — base level; with `margin=0.15` it gives the *hysteresis level*.
* `escalation.resolve_level(...)` — floor → +1 escalation (saturated ground / compound) → cap → disagreement flag.
* `lifecycle.step(existing, observation)` — pure decision (open / upgrade / count_below / downgrade / stand_down / reactivate); `scanner.apply_lifecycle` persists it.

## 4. The agent (`app/agent/`)

```mermaid
flowchart TD
    START(["Trigger fired, or officer clicks Run planner"]) --> ASSESS["ASSESS (code): get_assessment + get_official_alerts → deterministic summary"]
    ASSESS --> GATHER["GATHER (LLM tool loop): retrieve_plan · get_generic_guidance · find_facilities · geocode_place · compute_safe_route"]
    GATHER -->|"no LLM"| UNAVAIL(["Retrieval-only: plan sections shown, 'temporarily unavailable'"])
    GATHER --> PLAN["PLAN (LLM structured output): actions with chunk_id + verbatim quote"]
    PLAN --> VERIFY{"VERIFY (code): schema · quote ⊂ page · numbers ∈ evidence · facilities exist · phase not passed<br/>then LLM critic"}
    VERIFY -->|"pass"| COMMS["COMMUNICATE: EN/TA SMS + CAP text (LLM) → code validates lengths, wording, numbers; builds CAP + briefing"]
    VERIFY -->|"fail: specific feedback (max 2 retries)"| PLAN
    VERIFY -->|"retries exhausted"| PARTIAL["Partial plan, problems marked"]
    COMMS --> WAIT(["Wait for human approval"])
    PARTIAL --> WAIT
```

* `tools.py` — the registry: strict JSON schemas, budget (20 calls), each call and result emitted to the trace; tool output wrapped as `<tool_output trust="untrusted-data">` (prompt-injection hygiene).
* `verify.py` — the hard checks; returns messages like `A4: quote not found on p. 117`.
* `graph.py` — the LangGraph `StateGraph`; `runner.py` — run lifecycle, SSE events, persistence, citation enrichment.
* `llm.py` — `LLMClient` interface; `AnthropicLLM` (adaptive thinking with summarised reasoning streamed to the trace, structured outputs, server-side refusal fallback) and `FailoverLLM` (primary → backup model).

## 5. Retrieval (`app/rag/`)

```mermaid
flowchart LR
    URL["Official PDF (URL or upload)"] --> DL["validate + SHA-256"]
    DL --> PARSE["PyMuPDF text by page"]
    PARSE -->|"no text layer"| OCR["Tesseract OCR (flag low confidence)"]
    PARSE --> CHUNK["chunk by heading, never across pages"]
    OCR --> CHUNK
    CHUNK --> EMB["fastembed vectors (or TF-IDF fallback)"]
    EMB --> STORE[("plan_chunks")]
    Q["query"] --> BM25["BM25"] & VEC["cosine"]
    STORE --> BM25 & VEC
    BM25 --> RRF["reciprocal rank fusion"]
    VEC --> RRF
    RRF --> GATE{"coverage ≥ 0.34 or cosine ≥ threshold"}
    GATE -->|"yes"| HITS["chunks with page + section"]
    GATE -->|"no"| NF["not_found → 'Not found in district plan'"]
```

## 6. Approval and publishing (`app/api/runs.py`)

1. Officer edits messages (`PATCH /api/communications/{id}`) — re-validated (GSM-7 ≤ 160, Tamil ≤ 134).
2. `POST …/confirmation` returns exactly what will be published plus a 5-minute token = HMAC(comm id, SHA-256 of the EN+TA text, expiry).
3. `POST …/approve` with the token: re-validates, rebuilds the CAP from the approved text, validates it against the OASIS XSD (status must be `Exercise`), creates the public alert, writes approvals to the append-only audit log. If the text changed after confirmation, the token no longer matches.

## 7. Traceability chain (why every number is real)

`UI number → /api/districts/{id} → assessments.probabilities → assessments.fetch_ids → source_fetches.raw_payload (+ SHA-256, URL, params, timestamp)`; `/api/assessments/{id}/recompute` re-derives every probability from that payload.
