# Pitch deck (7 slides) and demo script

> Numbers in **[brackets]** change daily: read them from the live system on the day. Every other number below
> was measured on real data (31 Oct 2026, `docs/evidence/`). Never round up, never invent (golden rule 1).

## Slide 1 — Title & Team
**T-Minus** — the anticipatory-action agent for district disaster management

*Forecast → probability → trigger → the district's own plan → checked actions → officer approval → done.*

[Team name] · [Member — role] · [Member — role] · [Member — role] · Forge Gen AI Buildathon · Theme 2

Visual: the live command map (`docs/evidence/01-command-map-live.png`) as a dark full-bleed background.
Say: "India forecasts disasters well. T-Minus is about the hours between the forecast and the flood."

## Slide 2 — The Problem & Real-World Context
**The warning arrives. The action doesn't.**
* Forecasts give days of lead time, yet that time is lost between the warning and the first action — Chennai 2015, Cyclone Michaung 2023, Cyclone Fengal 2024.
* The bottleneck is human, often at night: read the warning → judge how likely it is → find what to do in a plan that runs to hundreds of pages (Chennai's: **805**) → check it can actually be done.
* Plans go stale and incomplete: the DM Act 2005 (s.31(4)) requires an annual review — Puducherry's current plan is from **2023**; in Chennai's plan, **116 of 142** listed shelters have no stated capacity.
* Food, medicine and power are invisible until they fail: godowns, cold storage and substations sit in flood paths with no pre-emptive plan.

Visual: a timeline — forecast (T-72 h) … warning … impact (T-0) … action (T+) — with the gap shaded.
Say: "The most valuable resource in a disaster is lead time, and today it's spent searching PDFs."

## Slide 3 — The Solution
**T-Minus tells a district what to do, whether it can do it, and what is stopping it — before impact.**
1. **Is it coming, and how sure are we?** 51 ECMWF forecasts → a real probability → WATCH / PREPARE / ACT.
2. **Where will it hit hardest?** Terrain + rivers × the live hazard → named localities.
3. **What should we do — and can we?** Actions quoted word for word from the district's own plan, each checked Ready / Unverified / Unavailable.
4. **Is it getting done?** Tasks with owners, deadlines and blockers, tracked to completion.

Visual: four app screenshots in a strip (futures chart, impact map, action card with readiness chips, tracker).
Say: "Not a chatbot. A pipeline from forecast to finished task, with an officer in control at every step."

## Slide 4 — Key Features
| | |
|---|---|
| **Live risk scanner** | 44 districts every hour: ECMWF 51-member ensemble, GloFAS rivers, official SACHET alerts |
| **Probabilistic triggers** | IMD rain thresholds, official-alert floors, hysteresis; only an officer stands a trigger down |
| **Plan → database** | Contacts, shelters, equipment, duties and SOPs extracted from the PDF with page and quote; gap check |
| **Impact map** | Low ground, depressions and rivers × live rain and wind → most-affected localities |
| **AI planner** | Reads the district's own plan; every action quotes its page verbatim or says "Suggested — not in plan" |
| **Readiness check** | Shelter, boats, pumps, staff, roads, backup power → Ready / Unverified / Unavailable |
| **Critical assets** | Godowns, cold storage, hospitals, substations at risk — with measures and where to move stock |
| **Approval & tracker** | Nothing sent without an officer; English + Tamil SMS, CAP 1.2 alert; every task tracked |

## Slide 5 — Technical Architecture & Innovation
**Diagram (left → right):** live sources (Open-Meteo ECMWF/GFS ensembles, GloFAS, Copernicus DEM, SACHET CAP feeds,
OpenStreetMap) → **data spine** (every API call logged with its raw response) → **engines, code only** (probability,
triggers, impact, readiness, asset risk) + **knowledge base** (plan PDFs → page-accurate chunks + structured records,
hybrid BM25 + vector search) → **agent** (LangGraph: ASSESS → GATHER → PLAN → VERIFY → COMMUNICATE; Claude or Gemini)
→ **deterministic verifier** → **officer approval** → tracker · CAP · SMS.

**Innovation**
* **Code computes, the LLM reasons** — the model never produces a number.
* **Verified citations** — every quote must be an exact substring of the cited page; a failed check goes back to the model with the specific problem.
* **Honest by design** — "Unverified" beats a false "Ready"; failures are shown, never filled in; every number traces to the raw API response ("Recompute").
* **Backtesting without hindsight** — replay uses forecasts exactly as they were issued at the time.
* **Provider-agnostic AI** — Claude or Gemini, with automatic failover.

Stack: FastAPI · LangGraph · SQLite/Postgres · React + MapLibre · 105 automated tests.

## Slide 6 — Proof on real data
* **Live scan:** **[44]** districts in **[6.1]** s from the ECMWF ensemble; current levels **[from the command map]**.
* **Replay — Cyclone Fengal, Viluppuram (2024), forecasts as issued:** PREPARE **30 h** before the observed 394.7 mm/24 h peak (WATCH 48 h, ACT 24 h); no false alarm in the quiet period.
* **Real plans:** Greater Chennai (805 pages) and Puducherry (396 pages) plans, plus 2 NDMA flood guidelines.
* **AI planner (Gemini) on real plans:** **18 of 18** actions quote their page verbatim; the verifier rejected one draft and the retry passed.
* **Plan as data (Chennai):** 2,266 contacts, 156 shelters, 71 equipment records — and the gaps.
* **Readiness on a real run:** Chennai 7 checks Ready, 5 Unverified; Puducherry all Unverified (2023 plan, overdue for review).

Visual: Fengal replay chart (`03`) + citation with highlighted quote (`09`) + readiness chips (`11`).

## Slide 7 — Roadmap, Ask & Live Demo
**Roadmap**
* **Pilot:** one DDMA (Chennai or Chengalpattu) through the north-east monsoon.
* **Next:** all 38 Tamil Nadu district plans; full resource optimiser (vehicles, storage capacity, safe routes); officer alerts on Telegram/WhatsApp — with the authorities' consent.
* **Then:** heat (WBGT) and cyclone-wind plug-ins on the same engine; IMD data access; native Tamil review.
* **Scale:** any Indian district = its plan PDF + config; any new hazard = one plug-in.

**Ask:** a pilot district and its asset registers (shelters, godowns, vehicles) — that turns "Unverified" into "Ready".

**Live demo (2–3 min):** command map → district futures + Recompute → Run planner (live trace) → actions with page
citations + readiness → approve → Tracker → plan gaps · impact · critical assets → Fengal replay.

Close: "India forecasts well. T-Minus turns forecasts into action — before impact."

---

## Demo script (2–3 minutes, the real system)

| Time | Show | Say |
|---|---|---|
| 0:00 | Command map → Sources (live timestamps) | "Every number here comes from a live official source — here's when each was fetched." |
| 0:20 | District → futures chart → *Recompute* | "ECMWF runs 51 versions of the future. In **[N]** of them this district crosses IMD's very-heavy line. Here's the raw data it came from." |
| 0:45 | *Run planner* → live trace | "The agent is reading this district's own disaster plan." (point at plan searches, VERIFY, any retry) |
| 1:10 | Actions → open a citation → readiness chips | "Every action quotes its page. And it tells you whether you can do it: pumps ready, backup power unverified." |
| 1:35 | Approve two actions → Tracker; block one with a reason | "Approved actions become tasks with owners and deadlines. Blocked work turns red." |
| 1:55 | Plan data (gaps) → Impact areas → Critical assets | "It also audits the plan, shows where it will hit hardest, and which godowns to protect." |
| 2:20 | Replay → Fengal | "On a real cyclone, using only forecasts that existed then, PREPARE fires **30** hours before the peak." |
| 2:40 | Close | "India forecasts well. T-Minus turns forecasts into action." |

### Live judging checklist
* A Gemini free-tier run takes 3–4 min: start the planner before you start talking (or use an earlier run).
* Run the setup scripts beforehand; open each district tab once so impact and assets are cached.
* If no district is above WATCH, show the all-clear (it's real), run the planner manually, then show Replay.
* Log in as officer (and admin for the outage simulator) before the demo. Never present a recording as the live system.
