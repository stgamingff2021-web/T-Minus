"""Gemini adapter: schema / tool / message conversion and rate-limit parsing (no network)."""
from app.agent.llm import to_anthropic_messages
from app.agent.llm_gemini import _retry_delay, to_function_declaration, to_gemini_contents, to_gemini_schema
from app.agent.schemas import PLAN_DRAFT_SCHEMA
from app.agent.tools import TOOL_SPECS


def test_schema_conversion_removes_unsupported_keywords_and_maps_nullable():
    g = to_gemini_schema(PLAN_DRAFT_SCHEMA)
    assert g["type"] == "OBJECT" and "additionalProperties" not in g
    action = g["properties"]["actions"]["items"]
    assert action["properties"]["responsible"] == {"type": "STRING", "nullable": True}
    assert action["properties"]["phase"]["enum"] == ["Do now", "T-48h", "T-24h", "T-6h"]
    assert action["properties"]["citations"]["items"]["properties"]["chunk_id"]["type"] == "STRING"
    assert "additionalProperties" not in str(g)


def test_function_declarations_omit_empty_parameters():
    decls = {t["name"]: to_function_declaration(t) for t in TOOL_SPECS}
    assert "parameters" not in decls["get_assessment"]
    p = decls["retrieve_plan"]["parameters"]
    assert p["type"] == "OBJECT" and p["properties"]["k"]["type"] == "INTEGER" and p["required"] == ["query", "k"]
    assert decls["find_facilities"]["parameters"]["properties"]["types"]["items"]["enum"]


def test_neutral_history_to_gemini_contents_replays_own_parts_and_names_tool_results():
    gem_parts = [{"thought": True, "text": "thinking summary"},
                 {"functionCall": {"name": "retrieve_plan", "args": {"query": "relief camps", "k": 3}, "id": "call_1"},
                  "thoughtSignature": "sig-abc"}]
    msgs = [
        {"role": "user", "content": "Gather evidence."},
        {"role": "assistant", "content": gem_parts, "provider": "gemini"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "name": "retrieve_plan",
                                      "content": "<tool_output>…</tool_output>", "is_error": False}]},
    ]
    c = to_gemini_contents(msgs)
    assert [x["role"] for x in c] == ["user", "model", "user"]
    assert c[1]["parts"] == [gem_parts[1]]  # thought summary dropped, signature kept verbatim
    fr = c[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "retrieve_plan" and fr["id"] == "call_1" and "tool_output" in fr["response"]["output"]


def test_failover_history_converts_between_providers():
    claude_turn = {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "signature": "x"},
        {"type": "text", "text": "Looking up camps."},
        {"type": "tool_use", "id": "toolu_1", "name": "retrieve_plan", "input": {"query": "camps", "k": 2}}]}
    parts = to_gemini_contents([{"role": "user", "content": "go"}, claude_turn])[1]["parts"]
    assert parts[0] == {"text": "Looking up camps."}
    assert parts[1]["functionCall"]["name"] == "retrieve_plan" and parts[1]["thoughtSignature"]
    gem_turn = {"role": "assistant", "provider": "gemini",
                "content": [{"functionCall": {"name": "geocode_place", "args": {"name": "Velachery"}, "id": "c9"}}]}
    a = to_anthropic_messages([{"role": "user", "content": "go"}, gem_turn,
                               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c9",
                                                             "name": "geocode_place", "content": "ok"}]}])
    assert a[1]["content"][0] == {"type": "tool_use", "id": "c9", "name": "geocode_place", "input": {"name": "Velachery"}}
    assert "name" not in a[2]["content"][0] and "provider" not in a[1]


def test_retry_delay_and_daily_quota_detection():
    err = {"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"},
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}
    assert _retry_delay(err) == (17.0, False)
    err["error"]["details"][1]["violations"][0]["quotaId"] = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    assert _retry_delay(err)[1] is True
