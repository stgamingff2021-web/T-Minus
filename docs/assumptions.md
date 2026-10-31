# Assumptions and verification notes

Where the specification left a detail open, the implementation made the assumption below. Items marked **(verify)** are external details to confirm against official documentation once the deployment environment can reach the source (the build sandbox's network policy blocked every live data host, so they could not be confirmed during the build).

## Data sources

| # | Assumption | Status |
|---|---|---|
| A-01 | Open-Meteo Ensemble API: per-member hourly variables are `precipitation` (control) and `precipitation_memberNN` (perturbed) for a single requested model; `ecmwf_ifs025` has 51 members, `gfs025` 31. The parser also accepts a `_<model>` suffix. | (verify) — `tests/integration/test_live_sources.py::test_open_meteo_ensemble_live` asserts 51 members |
| A-02 | Open-Meteo returns hourly values for every hour (3-/6-hourly model steps are distributed to hourly). If more than half the hours in the next 72 h are empty for all members, the response is rejected as malformed and the next ensemble model is used. | Guarded in code |
| A-03 | Model run time comes from Open-Meteo's static `meta.json` (`last_run_initialisation_time`, dataset names `ecmwf_ifs025_ensemble`, `ncep_gefs025`). When unavailable, run identity is a **data fingerprint**: if the ensemble mean at shared future timestamps is unchanged since the previous scan, it is the same run. Hysteresis counts runs by this identity, so a re-scan never counts twice. | (verify) meta path; fingerprint fallback tested |
| A-04 | GloFAS baseline uses the Flood API with `models=consolidated_v4` for 1984-01-01 → 2022-07-31; falls back to the default archive if that model name is rejected. P95/P99 are computed from valid daily values (≥ 365 required). | (verify) model name |
| A-05 | GloFAS run identity = hash of the forecast median series (the series changes only with a new run; at most one spurious "new run" per day when the window rolls over midnight UTC). | Documented |
| A-06 | SACHET: `SACHET_FEED_URL` defaults to `https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml`. Items may embed CAP or link to it; when the CAP document cannot be fetched, the RSS summary is used and flagged `summary_only`. Colour: an explicit "red/orange/yellow" word in parameters/headline/description wins; otherwise CAP severity Extreme→red, Severe→orange, Moderate→yellow. | (verify) feed URL and populated fields |
| A-07 | Escalation only amplifies an existing signal: saturated ground or a compound hazard raises WATCH→PREPARE etc., but never lifts Quiet to WATCH. | Spec ambiguity resolved conservatively |
| A-08 | For the disagreement comparison only, a yellow official alert counts as WATCH, and "no official alert" counts as Quiet — so an ensemble ACT with no official alert is flagged "Sources disagree" (informative for officers). When SACHET is unreachable, no disagreement is computed and "official alerts unavailable" is shown instead. | Documented |
| A-09 | The rolling 24-h accumulation uses the ensemble's own past day (`past_days=1`) so a 24-h total ending in the first forecast hours is complete; the trigger window is `[current hour, current hour + within_h]`. | Documented |
| A-10 | Antecedent rainfall = sum of the Forecast API's hourly precipitation over the 72 hours before the current hour (`past_days=3`); missing hours make it unknown (never zero). | Documented |
| A-11 | The district's representative monitor point is its headquarters town (config); river points are approximate and nudged ±0.1° to the GloFAS grid when null. | Config |
| A-12 | Overpass shelters exclude `shelter_type` public_transport / picnic_shelter / lean_to / basic_hut / field_shelter (bus stops are not relief shelters); community centres and event venues are included as candidate relief locations. | Documented |
| A-13 | District boundaries: OSM admin_level=5 relations per state (ISO 3166-2), matched to config names/aliases (fuzzy ≥ 88); geoBoundaries ADM2 as fallback. Unmatched districts render as HQ points. | `scripts/fetch_boundaries.py` |

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
| B-09 | LLM: Claude through the official Anthropic SDK (`claude-opus-5-5`, adaptive thinking, structured outputs, server-side refusal fallbacks) with `claude-sonnet-5-5` as the backup model behind one provider-agnostic interface (`app/agent/llm.py`). A second provider can be added by implementing `LLMClient`. |
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
| D-01 | Exact official PDF links for the TNSDMA DDMPs, the GCC city plan and NDMA guidelines must be copied from the official portals during setup (`config/plans.yaml` lists the landing pages; no link is guessed). |
| D-02 | Chunks never cross a page boundary; section headings are detected from numbering/capitalisation and carried across pages. |
| D-03 | Pages without a text layer are OCR'd with Tesseract (English); pages with mean confidence < 70 are flagged for review. |
