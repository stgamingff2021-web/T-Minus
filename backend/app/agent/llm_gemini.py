"""Google Gemini provider for the agent's provider-agnostic :class:`LLMClient` interface (spec §9.4).

Native REST API (``models/{model}:generateContent``) over httpx:

* auth via the ``x-goog-api-key`` header — the key never appears in a URL or a log line;
* tools -> ``functionDeclarations`` (JSON Schema converted to Gemini's OpenAPI-subset
  ``Schema``: upper-case types, ``nullable`` instead of ``anyOf [T, null]``, no
  ``additionalProperties``); tool results -> ``functionResponse`` parts;
* structured outputs -> ``responseMimeType: application/json`` + ``responseSchema``;
* thinking -> ``thinkingConfig.includeThoughts`` (thought summaries are streamed into the
  live trace); the model's own parts — including ``thoughtSignature`` — are replayed
  unchanged on the next turn, as Gemini requires for multi-turn function calling;
* HTTP 429: waits the server's ``retryDelay`` (bounded) and retries; daily quota
  exhaustion is reported as unavailable instead of retried. Safety blocks raise
  :class:`LLMRefused`.

Message format: the agent keeps one neutral, append-only history (user text, assistant
turns tagged with the provider that produced them, ``tool_result`` blocks carrying the
tool name). This module converts it to Gemini ``contents``; ``llm.py`` converts it for Claude.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from app.agent.llm import LLMRefused, LLMResult, LLMUnavailable
from app.sources import registry
from app.tls import ssl_verify

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
EFFORT_TO_LEVEL = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
SAFETY_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}
_SCHEMA_KEEP = ("required", "enum", "description", "format", "nullable", "minItems", "maxItems")


def to_gemini_schema(s: Any) -> Any:
    """JSON Schema (as used for Claude structured outputs/tools) -> Gemini OpenAPI-subset Schema."""
    if isinstance(s, list):
        return [to_gemini_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    if "anyOf" in s:
        options = s["anyOf"]
        non_null = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
        if len(non_null) == 1 and len(non_null) < len(options):
            out = to_gemini_schema(non_null[0])
            out["nullable"] = True
            if "description" in s:
                out["description"] = s["description"]
            return out
        return {"anyOf": [to_gemini_schema(o) for o in options]}
    out: dict[str, Any] = {}
    for k, v in s.items():
        if k == "type":
            if isinstance(v, list):  # ["string", "null"]
                types = [t for t in v if t != "null"]
                out["type"] = types[0].upper() if types else "STRING"
                if "null" in v:
                    out["nullable"] = True
            else:
                out["type"] = str(v).upper()
        elif k == "properties":
            out["properties"] = {pk: to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = to_gemini_schema(v)
        elif k in _SCHEMA_KEEP:
            out[k] = v
    if out.get("required") == []:
        out.pop("required")
    return out


def to_function_declaration(tool: dict) -> dict:
    decl = {"name": tool["name"], "description": tool.get("description", "")}
    schema = tool.get("input_schema") or {}
    if schema.get("properties"):
        decl["parameters"] = to_gemini_schema(schema)
    return decl


def _anthropic_blocks_to_parts(blocks: list) -> list[dict]:
    parts: list[dict] = []
    for b in blocks:
        t = b.get("type") if isinstance(b, dict) else None
        if t == "text" and b.get("text"):
            parts.append({"text": b["text"]})
        elif t == "tool_use":
            # Foreign function call (after a provider failover): Gemini documents this placeholder
            # signature for calls it did not generate itself.
            parts.append({"functionCall": {"name": b["name"], "args": b.get("input") or {}, "id": b.get("id")},
                          "thoughtSignature": "skip_thought_signature_validator"})
    return parts


def to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Neutral agent history -> Gemini ``contents``."""
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "user":
            if isinstance(content, str):
                parts = [{"text": content}]
            else:
                parts = []
                for b in content or []:
                    t = b.get("type")
                    if t == "text":
                        parts.append({"text": b.get("text", "")})
                    elif t == "tool_result":
                        resp = b.get("content")
                        payload = {"output": resp if isinstance(resp, str) else json.dumps(resp, default=str)}
                        if b.get("is_error"):
                            payload["error"] = True
                        fr: dict[str, Any] = {"name": b.get("name") or "tool", "response": payload}
                        if b.get("tool_use_id"):
                            fr["id"] = b["tool_use_id"]
                        parts.append({"functionResponse": fr})
            if parts:
                out.append({"role": "user", "parts": parts})
        elif role == "assistant":
            if m.get("provider") == "gemini":
                parts = [p for p in (content or []) if not p.get("thought")]
            elif isinstance(content, str):
                parts = [{"text": content}]
            else:
                parts = _anthropic_blocks_to_parts(content or [])
            if parts:
                out.append({"role": "model", "parts": parts})
        elif role == "system" and isinstance(content, str):
            out.append({"role": "user", "parts": [{"text": f"[operator instruction] {content}"}]})
    return out


def _retry_delay(err: dict) -> tuple[float | None, bool]:
    """(seconds to wait, is_daily_quota) from a Gemini 429 error body."""
    daily = False
    delay = None
    for d in (err.get("error", {}) or {}).get("details", []) or []:
        typ = d.get("@type", "")
        if typ.endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                delay = float(m.group(1))
        if typ.endswith("QuotaFailure"):
            for v in d.get("violations", []) or []:
                if "perday" in (v.get("quotaId", "") + v.get("quotaMetric", "")).lower().replace("_", ""):
                    daily = True
    return delay, daily


class GeminiLLM:
    provider = "gemini"

    def __init__(self, model: str, registry_key: str, api_key: str, timeout_s: float = 180.0,
                 max_rate_limit_wait_s: float = 45.0):
        self.model = model
        self.name = model
        self.registry_key = registry_key
        self._key = api_key
        self._timeout = timeout_s
        self._max_wait = max_rate_limit_wait_s
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=15.0), trust_env=True,
                                             verify=ssl_verify())
        return self._client

    def _fail(self, msg: str, t0: float) -> None:
        registry.observe(self.registry_key, status="error", error=msg[:200],
                         latency_ms=int((time.perf_counter() - t0) * 1000))

    async def _post(self, body: dict, t0: float) -> dict:
        url = API.format(model=self.model)
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        attempts = 0
        while True:
            attempts += 1
            try:
                r = await self._http().post(url, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempts < 2:
                    continue
                self._fail(f"{type(exc).__name__}", t0)
                raise LLMUnavailable(f"{self.model}: {type(exc).__name__} talking to the Gemini API") from exc
            if r.status_code == 200:
                return r.json()
            try:
                err = r.json()
            except ValueError:
                err = {"error": {"message": r.text[:200]}}
            msg = (err.get("error") or {}).get("message", "")[:240]
            if r.status_code == 429:
                delay, daily = _retry_delay(err)
                if daily or attempts >= 3:
                    self._fail("rate limited (daily quota)" if daily else "rate limited", t0)
                    raise LLMUnavailable(f"{self.model}: Gemini rate limit — {msg}")
                await asyncio.sleep(min(self._max_wait, (delay or 5.0) + 0.5))
                continue
            if r.status_code >= 500 and attempts < 3:
                await asyncio.sleep(2.0 * attempts)
                continue
            if r.status_code == 400 and "thinking" in msg.lower() and "thinkingLevel" in json.dumps(body):
                body["generationConfig"]["thinkingConfig"].pop("thinkingLevel", None)  # model without levels
                continue
            label = {400: "bad request", 401: "authentication failed — check GEMINI_API_KEY",
                     403: "permission denied — check GEMINI_API_KEY", 404: "model not found"}.get(r.status_code,
                                                                                               f"HTTP {r.status_code}")
            self._fail(label, t0)
            raise LLMUnavailable(f"{self.model}: {label}: {msg}")

    async def chat(self, *, system: str, messages: list[dict], tools: list[dict] | None = None,
                   output_schema: dict | None = None, max_tokens: int = 16000, effort: str | None = None) -> LLMResult:
        if registry.is_chaos(self.registry_key):
            registry.observe(self.registry_key, status="simulated_outage", simulated=True,
                             error="Simulated outage (admin chaos switch)")
            raise LLMUnavailable(f"{self.model}: simulated outage (admin chaos switch)")
        from app.config import get_settings

        gen: dict[str, Any] = {"maxOutputTokens": min(int(max_tokens), 65536),
                               "thinkingConfig": {"includeThoughts": True}}
        level = EFFORT_TO_LEVEL.get((effort or get_settings().llm_effort or "").lower())
        if level:
            gen["thinkingConfig"]["thinkingLevel"] = level
        if output_schema is not None:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = to_gemini_schema(output_schema)
        body: dict[str, Any] = {"contents": to_gemini_contents(messages), "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [to_function_declaration(t) for t in tools]}]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        t0 = time.perf_counter()
        data = await self._post(body, t0)
        dur = int((time.perf_counter() - t0) * 1000)
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            registry.observe(self.registry_key, status="ok", latency_ms=dur)
            raise LLMRefused(f"{self.model}: prompt blocked ({block})")
        cands = data.get("candidates") or []
        if not cands:
            self._fail("empty response", t0)
            raise LLMUnavailable(f"{self.model}: empty response (no candidates)")
        cand = cands[0]
        finish = cand.get("finishReason")
        registry.observe(self.registry_key, status="ok", latency_ms=dur)
        registry.set_model_run(self.registry_key, data.get("modelVersion") or self.model)
        if finish in SAFETY_REASONS:
            raise LLMRefused(f"{self.model}: response blocked ({finish})")
        parts = (cand.get("content") or {}).get("parts") or []
        text_parts, tool_uses, thinking = [], [], []
        for i, p in enumerate(parts):
            if p.get("thought"):
                if p.get("text"):
                    thinking.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_uses.append({"id": fc.get("id") or f"call_{i}_{fc.get('name')}", "name": fc.get("name"),
                                  "input": fc.get("args") or {}})
            elif p.get("text"):
                text_parts.append(p["text"])
        usage = data.get("usageMetadata") or {}
        result = LLMResult(
            content=[p for p in parts if not p.get("thought")],
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason="tool_use" if tool_uses else ("max_tokens" if finish == "MAX_TOKENS" else "end_turn"),
            model=data.get("modelVersion") or self.model,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            thinking=thinking,
            duration_ms=dur,
            provider="gemini",
        )
        if output_schema is not None:
            if finish == "MAX_TOKENS":
                raise LLMUnavailable(f"{self.model}: structured output truncated (MAX_TOKENS)")
            try:
                result.parsed = json.loads(result.text)
            except json.JSONDecodeError as exc:
                raise LLMUnavailable(f"{self.model}: structured output was not valid JSON") from exc
        return result
