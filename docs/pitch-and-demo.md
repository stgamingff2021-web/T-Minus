# Pitch deck (6 slides) and demo script

> Numbers in **[brackets]** must be read from the live system or the Replay page on the day — never rounded up, never invented (golden rule 1).

## Slide 1 — Title
**T-Minus** · The anticipatory-action agent for district disaster management
*India forecasts well. T-Minus turns forecasts into action — before the disaster, not after.*
Team names · Forge Gen AI Buildathon · Theme 2

## Slide 2 — The problem
* IMD and NDMA warn well; districts still act after impact.
* The bottleneck is the step between a warning and an action: interpret it, judge likelihood, search a 200–400-page District Disaster Management Plan — under time pressure, often at night.
* Forecast lead time, the most valuable resource in a disaster, gets wasted. (Michaung 2023, Wayanad 2024.)

## Slide 3 — The solution (one breath)
51 parallel forecasts → a real probability → a pre-agreed trigger → an agent that reads the district's own plan → cited actions on a countdown → human approval.
* Live scanner over all 38 Tamil Nadu districts + watch-list
* WATCH / PREPARE / ACT triggers (danger level + probability + lead time), official-alert floors, hysteresis
* Agent: plan actions phased Do now · T-48h · T-24h · T-6h, every action citing a page verbatim
* English + Tamil SMS drafts, CAP 1.2 alert, one-page briefing — nothing sent without an officer

## Slide 4 — Architecture and innovation
* Code computes, the LLM reasons: tested ensemble maths; the agent never sees raw members and never produces a number
* LangGraph state graph with a deterministic VERIFY: exact-substring citations, number provenance, facility existence, phase sanity → specific retry feedback
* Hybrid retrieval (BM25 + vectors + RRF) with a "not found" gate
* Every number traceable to the stored raw API response ("Recompute from raw")
* Chaos switch + honest failure states; replay with forecasts *as issued* (no look-ahead)

## Slide 5 — Results
* Live: **[N]** districts scanned in **[s]** s; current levels **[from command map]**
* Replay, Cyclone Fengal — Viluppuram (forecasts as issued by 8 global models): **PREPARE would have fired 30 h before the 394.7 mm/24 h peak** (WATCH 48 h, ACT 24 h); quiet-period false alarms: **none** — measured 2026-10-31, see `docs/evidence/03-replay-fengal-live.png`
* 76 automated tests incl. VERIFY rejection/retry, approval gate, CAP XSD validity

## Slide 6 — Roadmap and ask
* Humid-heat (WBGT) plug-in on the same engine → heat action plans
* All Indian districts: a new district = plan PDF + config; a new hazard = one plug-in
* Native Tamil review workflow; Telegram/SACHET integration only with authorities' consent
* Ask: pilot with one DDMA (Chennai / Chengalpattu) for the 2026 north-east monsoon

---

## Demo script (2–3 minutes, the real system)

| Time | Show | Say |
|---|---|---|
| 0:00 | Title; a Chennai flood reference | "India knows the storm is coming. Districts still act after it hits." |
| 0:15 | Command map → Sources page (live timestamps, fetch log) | "Every number here comes from a live official source. Here's when each one was fetched." |
| 0:35 | District view → futures chart → *Recompute from raw* | "ECMWF runs 51 versions of the future. In **[N]** of them this district crosses IMD's very-heavy line within **[W]** hours. That's our trigger — and here's the raw response it came from." |
| 1:00 | *Run planner* → live agent trace | "The agent is now reading this district's own disaster plan." (point at retrieve_plan calls, VERIFY, any retry) |
| 1:25 | Action timeline → open a citation | "Every action cites the page it came from. If it isn't in the plan, it says so." |
| 1:50 | Edit + approve; Tamil message counters; CAP XSD badge; Approve & publish dialog | "Nothing leaves without an officer's approval — in Tamil and English, in the national alert standard." |
| 2:10 | Sources → chaos switch on ECMWF → Scan now | "Watch what happens when a data source dies." (GFS fallback, simulated outage label) |
| 2:25 | Replay → Fengal | "On a real past cyclone, using only forecasts that existed at the time, T-Minus would have fired PREPARE **30** hours before peak rain." (re-check the Replay page on the day) |
| 2:45 | Close | "India forecasts well. T-Minus turns forecasts into action." |

### Live judging checklist
* Open with the Sources page to show data is live; keep `/api/health` open in a second tab.
* If no district is above WATCH, show the all-clear state (it's real), run the planner manually on a district, then show Replay.
* Log in as admin before the demo (chaos switch). Never present a recording as the live system.
