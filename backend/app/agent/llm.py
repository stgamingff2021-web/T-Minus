"""Provider-agnostic LLM interface (spec §9.4): Claude and Gemini implementations.

The agent only talks to :class:`LLMClient`. ``GeminiLLM`` (``llm_gemini.py``) implements it
with Google's Gemini REST API. ``AnthropicLLM`` implements it with the official Anthropic SDK:

* model ``LLM_MODEL`` (default ``claude-opus-5-5``), adaptive thinking with summarised
  reasoning (streamed into the live trace), effort from ``LLM_EFFORT``;
* server-side refusal fallbacks (``fallbacks="default"``) so a safety-classifier
  decline is retried on Anthropic's recommended model instead of failing the run;
* structured outputs (``output_config.format``) for the plan / critic / comms drafts.

Outage handling (§8.2): the SDK retries 408/409/429/5xx itself; if the primary model
still fails, :class:`FailoverLLM` switches to ``LLM_BACKUP_MODEL``. With no key at
all, ``get_llm()`` returns None and the planner reports "temporarily unavailable"
while the scanner, triggers and map keep working.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings
from app.sources import registry

log = logging.getLogger("tminus.llm")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    """The provider could not serve the request (outage, auth, rate limit, simulated outage)."""


class LLMRefused(Exception):
    """The model declined (stop_reason == "refusal") even after server-side fallback."""


@dataclass
class LLMResult:
    content: list[Any]  # provider-native content blocks (passed back unchanged in tool loops)
    text: str
    tool_uses: list[dict]
    stop_reason: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking: list[str] = field(default_factory=list)
    duration_ms: int = 0
    parsed: Any = None
    provider: str = "anthropic"


class LLMClient(Protocol):
    name: str

    async def chat(self, *, system: str, messages: list[dict], tools: list[dict] | None = None,
                   output_schema: dict | None = None, max_tokens: int = 16000,
                   effort: str | None = None) -> LLMResult: ...


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Neutral agent history -> Claude Messages API format.

    Strips bookkeeping keys (``provider`` on assistant turns, ``name`` on tool results) and
    converts turns produced by another provider (after a failover) into text/tool_use blocks.
    """
    out = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "assistant" and m.get("provider") not in (None, "anthropic"):
            blocks = []
            for i, p in enumerate(content or []):
                if p.get("text") and not p.get("thought"):
                    blocks.append({"type": "text", "text": p["text"]})
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    blocks.append({"type": "tool_use", "id": fc.get("id") or f"call_{i}_{fc.get('name')}",
                                   "name": fc.get("name"), "input": fc.get("args") or {}})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]})
            continue
        if role == "user" and isinstance(content, list):
            content = [{k: v for k, v in b.items() if k != "name"} if b.get("type") == "tool_result" else b
                       for b in content]
        out.append({"role": role, "content": content})
    return out


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, model: str, registry_key: str, api_key: str, timeout_s: float):
        import anthropic

        self.model = model
        self.name = model
        self.registry_key = registry_key
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    async def chat(self, *, system: str, messages: list[dict], tools: list[dict] | None = None,
                   output_schema: dict | None = None, max_tokens: int = 16000,
                   effort: str | None = None) -> LLMResult:
        a = self._anthropic
        if registry.is_chaos(self.registry_key):
            registry.observe(self.registry_key, status="simulated_outage", error="Simulated outage (admin chaos switch)",
                             simulated=True)
            raise LLMUnavailable(f"{self.model}: simulated outage (admin chaos switch)")
        output_config: dict[str, Any] = {"effort": effort or get_settings().llm_effort}
        if output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": output_schema}
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=to_anthropic_messages(messages),
            thinking={"type": "adaptive", "display": "summarized"},
            output_config=output_config,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if tools:
            kwargs["tools"] = tools
        t0 = time.perf_counter()
        try:
            resp = await self._client.beta.messages.create(**kwargs)
        except a.AuthenticationError as exc:
            self._fail("authentication failed", t0)
            raise LLMUnavailable(f"{self.model}: authentication failed") from exc
        except a.PermissionDeniedError as exc:
            self._fail("permission denied", t0)
            raise LLMUnavailable(f"{self.model}: permission denied") from exc
        except a.NotFoundError as exc:
            self._fail("model not found", t0)
            raise LLMUnavailable(f"{self.model}: model not found") from exc
        except a.BadRequestError as exc:
            self._fail(f"bad request: {exc.message}"[:200], t0)
            raise LLMUnavailable(f"{self.model}: bad request: {exc.message}"[:300]) from exc
        except a.RateLimitError as exc:
            self._fail("rate limited", t0)
            raise LLMUnavailable(f"{self.model}: rate limited") from exc
        except a.APIStatusError as exc:
            self._fail(f"HTTP {exc.status_code}", t0)
            raise LLMUnavailable(f"{self.model}: HTTP {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            self._fail("connection error", t0)
            raise LLMUnavailable(f"{self.model}: connection error") from exc
        dur = int((time.perf_counter() - t0) * 1000)
        registry.observe(self.registry_key, status="ok", latency_ms=dur)
        if resp.stop_reason == "refusal":
            raise LLMRefused(f"{resp.model} declined the request")
        text_parts, tool_uses, thinking = [], [], []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input})
            elif btype == "thinking" and getattr(block, "thinking", ""):
                thinking.append(block.thinking)
        usage = getattr(resp, "usage", None)
        result = LLMResult(
            content=[b.model_dump(exclude_none=True) for b in resp.content],
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=resp.stop_reason,
            model=resp.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            thinking=thinking,
            duration_ms=dur,
        )
        if output_schema is not None:
            if resp.stop_reason == "max_tokens":
                raise LLMUnavailable(f"{self.model}: structured output truncated (max_tokens)")
            try:
                result.parsed = json.loads(result.text)
            except json.JSONDecodeError as exc:
                raise LLMUnavailable(f"{self.model}: structured output was not valid JSON") from exc
        return result

    def _fail(self, msg: str, t0: float) -> None:
        registry.observe(self.registry_key, status="error", error=msg,
                         latency_ms=int((time.perf_counter() - t0) * 1000))


class FailoverLLM:
    """Primary model with automatic switch to a backup model on provider failure."""

    def __init__(self, primary: LLMClient, backup: LLMClient | None):
        self.primary = primary
        self.backup = backup
        self.active: LLMClient = primary
        self.events: list[str] = []

    @property
    def name(self) -> str:
        return self.active.name

    async def chat(self, **kw) -> LLMResult:
        try:
            return await self.active.chat(**kw)
        except LLMUnavailable as exc:
            if self.backup is None or self.active is self.backup:
                raise
            self.events.append(f"Primary model failed ({exc}); switched to backup {self.backup.name}.")
            self.active = self.backup
            return await self.backup.chat(**kw)


def llm_configured() -> bool:
    s = get_settings()
    return bool(s.anthropic_api_key or s.gemini_api_key)


def _provider_order() -> list[str]:
    s = get_settings()
    pref = (s.llm_provider or "auto").lower()
    have = [p for p, key in (("anthropic", s.anthropic_api_key), ("gemini", s.gemini_api_key)) if key]
    if pref in ("anthropic", "gemini"):
        return [pref] if pref in have else []
    return have  # auto: Claude first when both keys exist, else whichever is configured


def get_llm() -> FailoverLLM | None:
    """Primary + backup behind one interface (spec §9.4).

    * only Claude:  LLM_MODEL, backup LLM_BACKUP_MODEL
    * only Gemini:  GEMINI_MODEL, backup GEMINI_BACKUP_MODEL
    * both (auto):  Claude primary, Gemini backup — survives a whole-provider outage
    """
    from app.agent.llm_gemini import GeminiLLM

    s = get_settings()
    order = _provider_order()
    if not order:
        registry.set_disabled("llm:primary", "Not configured")
        registry.set_disabled("llm:backup", "Not configured")
        return None

    def make(provider: str, model: str, key: str):
        if provider == "anthropic":
            return AnthropicLLM(model, key, s.anthropic_api_key, s.llm_timeout_s)
        return GeminiLLM(model, key, s.gemini_api_key, s.gemini_timeout_s)

    primary_provider = order[0]
    primary = make(primary_provider, s.llm_model if primary_provider == "anthropic" else s.gemini_model, "llm:primary")
    backup = None
    if len(order) > 1:
        other = order[1]
        backup = make(other, s.llm_model if other == "anthropic" else s.gemini_model, "llm:backup")
    else:
        bmodel = s.llm_backup_model if primary_provider == "anthropic" else s.gemini_backup_model
        if bmodel and bmodel != primary.name:
            backup = make(primary_provider, bmodel, "llm:backup")
    registry.set_disabled("llm:primary", None)
    registry.set_disabled("llm:backup", None if backup else "no backup model configured")
    return FailoverLLM(primary, backup)
