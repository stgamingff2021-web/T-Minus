# Live evidence — 31 October 2026

Screenshots of the real system running against the **live** public APIs (no fixtures), captured from a local run
(`uvicorn`, SQLite, scheduler on) in the build environment on 2026-10-31 between 16:40 and 17:05 IST.
Numbers are what the live sources reported at that time; they will differ on any other day.

| File | What it shows |
|---|---|
| `01-command-map-live.png` | All 44 monitored districts on real OpenStreetMap district boundaries (fetched via Nominatim, ODbL) over the CARTO basemap. ECMWF ensemble (run 2026-10-30 18:00 UTC): no district reaches the very-heavy-rain WATCH threshold; 7 districts are **WATCH from the GloFAS river rule** (ensemble maximum above the cell's own 1984–2022 P95). The STALE banner is real: in that scan 4 districts hit Open-Meteo's rate limit on the shared build IP. |
| `02-chennai-futures-and-river-live.png` | Chennai futures chart: all 51 ECMWF members' 24-h accumulations (wettest member ≈ 100 mm/24 h near 3 Nov, below the 115.6 mm line → 0 of 51). GloFAS panels for the snapped river cells; Adyar and Cooum share one ~5 km cell (the urban-channel caveat). |
| `03-replay-fengal-live.png` | Replay of Cyclone Fengal for Viluppuram from forecasts **as issued** (Open-Meteo Previous Runs API, 8 global models) vs ERA5 reanalysis ground truth: WATCH 48 h, **PREPARE 30 h**, ACT 24 h before the observed 394.7 mm/24 h peak (1 Dec 2024 12:00 UTC); no false alarm in the 1–9 Oct 2024 quiet period. |
| `04-planner-retrieval-only-puducherry-live.png` | Planner run for Puducherry with **no LLM key** configured: live assessment, live SACHET check, real retrieval from the ingested *Puducherry Disaster Management Action Plan 2023*, the Overpass outage surfaced as a tool error, and the honest "temporarily unavailable" state. |
| `05-sources-live.png` | Sources panel with live statuses, latencies and model-run time from `meta.json`; failures (rate limits / blocked hosts in the build environment) shown as failures. |
| `06-plans-ingested-live.png` | Knowledge base with the real GCC City Disaster Management Perspective Plan 2025 (805 pages → 4,601 chunks) and the Puducherry DM Action Plan 2023 (396 pages → 1,726 chunks), with SHA-256 and source URLs. |
| `07-gemini-agent-plan-chennai-live.png` | **Full agent run on a real LLM** (Gemini, served by gemini-3.6-flash) against the 805-page GCC plan: 13 tool calls; VERIFY rejected attempt 1 ("A6: location … not in the plan text") and attempt 2 passed — 9 actions, 9 verbatim citations (pp. 24–718), countdown-phased, each with Approve / Edit / Reject. (The attempt-1 rejection exposed a whitespace bug in the verifier's name check, since fixed with a regression test.) |
| `08-gemini-agent-plan-puducherry-live.png` | Agent run for Puducherry against the real DM Action Plan 2023: 9 actions verified on the first attempt (pp. 27–351); when the facility lookup timed out the agent adapted on its own; gaps listed as "Not found in district plan". |
| `09-citation-verbatim-quote-live.png` | Citation modal: the quote highlighted inside the actual text of GCC CDMP 2025 page 24. |
| `10-plan-data-chennai.png` | Structured plan data for Chennai, extracted by code from the real GCC plan: gaps (33 malformed numbers; 116 of 142 shelters without capacity), shelters with capacity and page links. |
| `11-readiness-on-live-run.png` | Readiness chips on the real Gemini Chennai run (Pumps: Ready from plan inventory; Backup power / Road access: Unverified) and task states (in progress, blocked, overdue). |
| `12-action-tracker.png` | The tracker across districts: counts, an overdue blocked task with its reason, departments, readiness. |
| `13-impact-hazard-profile.png` | Impact tab: the live ensemble intensity profile; terrain was rate-limited on the build IP, so no cells are drawn and the reason is shown. |

Not shown live from the build environment: OpenStreetMap facilities (every Overpass mirror timed out through that
network), TNSDMA-hosted district plans (host blocked there), and a public-message/CAP draft (no district crossed
30% probability that day, so — correctly — none was drafted). Capture those on the deployed system.
