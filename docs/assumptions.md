# Assumptions and verification notes

Where the specification left a detail open, the implementation made the assumption below. External details were **verified against the live services on 2026-10-31** (see `docs/evidence/`); items still marked **(verify)** could not be reached from the build environment.

## Data sources

| # | Assumption | Status |
|---|---|---|
| A-01 | Open-Meteo Ensemble API: per-member hourly variables are `precipitation` (control) and `precipitation_memberNN` (perturbed) for a single requested model; `ecmwf_ifs025` has 51 members, `gfs025` 31. The parser also accepts a `_<model>` suffix. | **Verified live** (51 / 31 members; `test_open_meteo_ensemble_live` passes) |
| A-02 | Open-Meteo returns hourly values for every hour (3-/6-hourly model steps are distributed to hourly). If more than half the hours in the next 72 h are empty for all members, the response is rejected as malformed and the next ensemble model is used. | **Verified live**: 192/192 hours filled, no nulls; 3-hourly totals spread evenly (0.2, 0.2, 0.2 …) |
| A-03 | Model run time comes from Open-Meteo's static `meta.json` (`last_run_initialisation_time`, dataset names `ecmwf_ifs025_ensemble`, `ncep_gefs025`, `dwd_icon_eps`). When unavailable, run identity is a **data fingerprint**: if the ensemble mean at shared future timestamps is unchanged since the previous scan, it is the same run. Hysteresis counts runs by this identity, so a re-scan never counts twice. | **Verified live**: `…/data/ecmwf_ifs025_ensemble/static/meta.json` → run 2026-10-30 18:00 UTC (available 03:12 UTC); run id `ecmwf_ifs025@2026-10-30T18:00Z` |
| A-04 | GloFAS baseline uses the Flood API with `models=consolidated_v4` for 1984-01-01 → 2022-07-31; falls back to the default archive if that model name is rejected. P95/P99 are computed from valid daily values (≥ 365 required). | **Verified live**: 14,092 days returned, 9,343 non-null (early years null); Cauvery at Karur P95 ≈ 1,073 m³/s |
| A-05 | GloFAS run identity = hash of the forecast median series (the series changes only with a new run; at most one spurious "new run" per day when the window rolls over midnight UTC). | Documented |
| A-06 | SACHET: per-state RSS feeds `…/cap_public_website/rss/<name>.xml` (TN = `rss_tamil`, PY = `rss_puducherry`, KL = `rss_kerala`, AP = `rss_andhra`, OR = `rss_odisha`, AS = `rss_assam`, MH = `rss_maharashtra`); the national `rss_india.xml` keeps only the latest 100 items of all states, so it is off by default. Items link to CAP 1.2 (`FetchXMLFile?identifier=…`, `cap:` prefix, English + regional info blocks — the English block is used). **No colour code and no inline polygon** are published (the "Polygon URL" file is WAF-protected, HTTP 403); district matching uses `areaDesc`/headline names ("Tenkasi, Virudhunagar districts of Tamil Nadu"); "LGD District Code" parameters are kept. Floors: only heavy-rain / flood / cyclone alerts (severity Severe→orange→PREPARE, Extreme→red→ACT; explicit colour words win); thunderstorm/lightning and light/moderate-rain nowcasts are shown as information only. | **Verified live** (TN feed: IMD Chennai nowcasts, 26–27 Oct) |
| A-07 | Escalation only amplifies an existing signal: saturated ground or a compound hazard raises WATCH→PREPARE etc., but never lifts Quiet to WATCH. | Spec ambiguity resolved conservatively |
| A-08 | For the disagreement comparison only, a yellow official alert counts as WATCH, and "no official alert" counts as Quiet — so an ensemble ACT with no official alert is flagged "Sources disagree" (informative for officers). When SACHET is unreachable, no disagreement is computed and "official alerts unavailable" is shown instead. | Documented |
| A-09 | The rolling 24-h accumulation uses the ensemble's own past day (`past_days=1`) so a 24-h total ending in the first forecast hours is complete; the trigger window is `[current hour, current hour + within_h]`. | Documented |
| A-10 | Antecedent rainfall = sum of the Forecast API's hourly precipitation over the 72 hours before the current hour (`past_days=3`); missing hours make it unknown (never zero). | Documented |
| A-11 | The district's representative monitor point is its headquarters town (config). River points are approximate: before computing a baseline each point is **snapped** to the GloFAS cell with the largest mean forecast discharge within ±0.05° (widening to ±0.1°). Verified necessary live: the configured "Cauvery at Srirangam" cell carried 0.46 m³/s. Points of one district that snap to the same cell are merged (Chennai's Adyar and Cooum share one ~5 km cell). | **Verified live** |
| A-12 | Overpass shelters exclude `shelter_type` public_transport / picnic_shelter / lean_to / basic_hut / field_shelter (bus stops are not relief shelters); community centres and event venues are included as candidate relief locations. | Documented |
| A-14 | Open-Meteo free-tier limits are per IP (per minute and per day) and long requests count as several calls (a 38-year daily baseline is heavy). Rate-limited calls (HTTP 429) are never retried within seconds, never interpreted as "no data/no river", and the baseline job waits out per-minute limits (script) or retries on a later scan. Observed live on the shared build IP: minutely and daily limits on the Flood, Forecast, Previous Runs and Archive APIs. | **Verified live** |
| A-13 | District boundaries: OSM admin_level=5 relations via Overpass; else the same OSM relations via **Nominatim** `polygon_geojson` (1 req/s; accepted only if the polygon contains the district HQ); else geoBoundaries ADM2 (hosted on GitHub LFS). Unmatched districts render as HQ points. | **Verified live**: 44/44 districts via Nominatim (Overpass mirrors timed out from the build network) |

## Agent and verification

| # | Assumption |
|---|---|
| B-01 | "Exact substring" is checked after Unicode NFC normalisation and collapsing whitespace runs (PDF text extraction inserts line breaks and double spaces); no other normalisation — quotes, punctuation and wording must match. |
| B-02 | Number check: every number in the plan text must appear in the evidence handed to the model — assessment JSON (and its rounded forms), official alert text, retrieved plan chunks, facility/geocode names, route figures — or be a phase constant (48/24/6), with ±0.5 tolerance. Numbers quoted from the plan ("10 boats") are therefore allowed only if that plan text was retrieved. |
| B-03 | Facility check: `location_refs` must be OSM ids from `find_facilities`, area ids from `geocode_place`, or names present in retrieved plan text; facility-like proper names in action text (e.g. "… Higher Secondary School") must also appear in plan text or OSM results (fuzzy ≥ 90). |
| B-04 | Phase check: "no action phased later than t\*" is implemented as "no action in a phase that has already passed" (T-48h is invalid when impact is 31 h away; its work belongs in Do now). When t\* is unknown (manual run below WATCH), all phases are allowed and a warning is shown. |
| B-05 | Manual runs below WATCH use level `QUIET` (not one of the trigger levels) and draft readiness actions; no public message is drafted below 30% probability. |
| B-06 | The officer-facing evidence summary is generated twice: deterministically by code (always shown) and by the LLM (number-checked). |
| B-07 | Public messages drafted by the agent that contain numbers not present in the evidence (e.g. "2 days" derived from 48 h) are stored as invalid and cannot be published until an officer edits them. |
| B-08 | The agent auto-runs for triggers that open/escalate at or above `AUTO_PLAN_MIN_LEVEL` (default PREPARE) to bound LLM cost; WATCH districts can be planned on demand. |
| B-09 | LLM: one provider-agnostic interface (`app/agent/llm.py`) with two implementations — Claude via the official Anthropic SDK (`claude-opus-5-5`, adaptive thinking, structured outputs, server-side refusal fallbacks; backup `claude-sonnet-5-5`) and Gemini via its REST API (`app/agent/llm_gemini.py`; `gemini-3.5-flash`, backup `gemini-flash-lite-latest`; thought summaries in the trace, thought signatures replayed, 429 retryDelay honoured). With both keys, Claude is primary and Gemini the backup. The agent keeps one neutral, append-only history that each adapter converts. Verified live with Gemini on 2026-10-31. |
| B-11 | The 120 s budget is treated as a *soft* timeout: GATHER stops at half of it, the optional LLM critic runs only inside it, and required PLAN retries may continue up to 2× the soft timeout so a verifier rejection still gets its retry. Each LLM call has its own 180 s timeout. |
| B-10 | Without fastembed's local model the vector half of hybrid search uses character n-gram TF-IDF; the active backend is reported in every retrieval result and in the plan warnings. |

## Communications and safety

| # | Assumption |
|---|---|
| C-01 | Public-message probability is shown as a round-half-up integer percentage ("very likely (75%)" for 74.5%); officer screens show one decimal ("74.5% (38 of 51)"). |
| C-02 | CAP `sent/effective/onset/expires` use IST (+05:30) because the XSD pattern forbids `Z`; `expires` = t\* + 24 h; `onset` = earliest plausible onset. `references` carry `sender,identifier,sent` of the previous published message for `Update`. |
| C-03 | CAP area = district name + simplified district polygon (≤ 100 points) when boundaries are loaded, otherwise a circle around the HQ with the configured search radius. |
| C-04 | Officer authentication uses two shared passcodes (officer, admin) from the environment and a display name; no personal data is stored. Sessions are HMAC-signed tokens (12 h). |
| C-05 | "Publish" writes to the app's own public alerts page only; CAP `status` is always `Exercise`. |
| C-06 | The CAP 1.2 XSD in `backend/app/comms/xsd/` is the official OASIS schema; the copy was obtained from the `capparselib` package because docs.oasis-open.org was unreachable from the build sandbox. `python -m scripts.fetch_cap_xsd` compares it with the OASIS original. |

## Plans

| # | Assumption |
|---|---|
| D-04 | Retrieval excludes table-of-contents pages (detected by repeated "numbered heading … page number" entries) — they match many queries but carry no actionable content. Relevance gate thresholds were calibrated on the real GCC Chennai CDMP 2025 and Puducherry DM Action Plan 2023 (`config/hazards.yaml`). |
| D-05 | Setup-script ingestion from an official URL allows larger files (`MAX_INGEST_MB`, default 150: city plans are 60–80 MB) than officer uploads through the UI (`MAX_UPLOAD_MB`, default 30, spec starting value). |

| # | Assumption |
|---|---|
| D-01 | Exact official PDF links for the TNSDMA DDMPs, the GCC city plan and NDMA guidelines must be copied from the official portals during setup (`config/plans.yaml` lists the landing pages; no link is guessed). |
| D-02 | Chunks never cross a page boundary; section headings are detected from numbering/capitalisation and carried across pages. |
| D-03 | Pages without a text layer are OCR'd with Tesseract (English); pages with mean confidence < 70 are flagged for review. |
