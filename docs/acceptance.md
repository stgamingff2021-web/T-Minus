# Acceptance criteria — evidence map

Automated evidence comes from `backend/tests` (run `python -m pytest -q`). Live evidence (screenshots, logs) must be captured on the deployed URL with real data — the build sandbox could not reach the live sources, so no live screenshots are included here.

| ID | Criterion | Automated evidence | Live check on the deployed system |
|---|---|---|---|
| AC-01 | ≥ 10 districts scanned in < 60 s, each traceable to a logged real API call | `test_scan_pipeline::test_scan_computes_levels_and_opens_exactly_one_trigger` (44 districts, raw payload stored); `test_live_sources::test_open_meteo_ensemble_live` (< 60 s) | `/api/health` → `last_scan.duration_s`; Sources page fetch log |
| AC-02 | Displayed probability = members above ÷ valid members, from the stored raw response | `test_ensemble_math::test_probability_worked_example_38_of_51`; scan test asserts `(40, 51)` straight from members | District page → **Recompute from raw** (`/api/assessments/{id}/recompute`, `all_match: true`) |
| AC-03 | Crossing PREPARE creates exactly one trigger; next scan doesn't duplicate; visible on map within one cycle | scan test (second scan, same run → 1 trigger, same `model_run_id`); `test_levels_escalation_lifecycle::test_same_level_next_scan_is_not_a_duplicate` | Command map + `/api/triggers?status=active` |
| AC-04 | 100% of actions cite an exact substring of the page or are labelled Suggested | `test_comms_verify_rag::test_verify_rejects_paraphrased_quote_with_page_number`, `test_agent_graph::test_agent_run_retries_on_verify_failure_then_passes` | Run page: “verified” badge; citation modal highlights the quote |
| AC-05 | No invented facilities | `test_verify_rejects_unknown_facilities` | Run page map layers (OSM ids) |
| AC-06 | Chaos on primary → backup used, failure shown; all off → stale banner, no new triggers | `test_ecmwf_outage_falls_back_to_gfs_and_is_recorded`, `test_all_sources_down_no_new_triggers_and_no_invented_values` | Sources page chaos switch (admin) → Scan now |
| AC-07 | District without a plan → "no district plan available" + labelled NDMA generic guidance | `test_district_without_plan_is_labelled` | Run planner on a district with no ingested plan |
| AC-08 | Nothing reaches the public page without Approve + confirmation; audit log records it | `test_approval_gate_and_audit` (bogus token rejected, invalid SMS blocks publish, audit entries) | Comms panel → Approve & publish; Audit page |
| AC-09 | CAP validates against OASIS CAP 1.2 XSD with status Exercise | `test_cap_builds_valid_exercise_xml_with_two_languages`, `test_cap_rejects_actual_status`, agent test validates the run's CAP | `/api/communications/{id}/cap.xml` → `X-CAP-Valid: true` |
| AC-10 | English ≤ 160, Tamil ≤ 134, both present and editable | `test_gsm7_*`, `test_ucs2_tamil_two_segments_limit`, approval test edits and re-validates | Comms panel counters |
| AC-11 | Two new model runs below (entry − margin) → stand-down recommendation, never automatic close | `test_stand_down_needs_two_new_model_runs_and_never_auto_closes`, `test_hysteresis_counts_new_model_runs_not_scans` | District page trigger history |
| AC-12 | Replay reports WATCH/PREPARE/ACT fire times vs peak (target PREPARE ≥ 24 h) + quiet-period check | `test_as_issued_series_never_uses_runs_after_the_replay_clock` (no look-ahead) | Replay page → Cyclone Fengal (record the real lead times in `docs/decisions.md`) |
| AC-13 | Every step/tool call in the UI within 2 s | agent test asserts the event stream (step, tool_call, verify, retry, done) | Run page live trace (events are published as they happen) |
| AC-14 | Garbage input handled | `/api/districts/resolve` fuzzy match; upload validation (`not_a_pdf`, `file_too_large`, `encrypted_pdf`); empty question → `empty_question`; unknown district → `unknown_district` | Plans page upload of a non-PDF / 50 MB file; Q&A with an empty question |
