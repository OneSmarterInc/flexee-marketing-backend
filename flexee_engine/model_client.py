"""Flexee — live model client adapter.

The AI layers (live_voice, advisor_voice, defense_grader) all take an injected callable
matching the ModelClient protocol from live_voice.py:

    model_client(system: str, user: str, temperature: float = 0.7) -> str

This module provides that callable for real vendors, provider-agnostic behind one factory,
plus a deterministic offline client for local dev. Nothing here is Flexee-specific — it just
turns (system, user, temperature) into text. Wire it in three places (see the handoff §3b):
    persona voice:  live_voice.live_persona_generator(make_model_client(...), pool_by_id)
    advisor voice:  advisor_voice.live_advisor_generator(make_model_client(...))
    defense grade:  defense_grader.grade_written_defense(text, packet, make_model_client(...), cache)

Config via env vars (or pass explicitly): FLEXEE_LLM_PROVIDER (anthropic|openai|offline),
FLEXEE_LLM_MODEL, and the vendor key (ANTHROPIC_API_KEY / OPENAI_API_KEY).

SDKs are imported lazily so this file loads even if neither SDK is installed; you only need the
one for the provider you choose (`pip install anthropic` or `pip install openai`).
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

ModelClient = Callable[[str, str, float], str]  # (system, user, temperature) -> str

DEFAULT_MAX_TOKENS = 400          # personas/advisors speak briefly; grader returns small JSON
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRIES = 2               # transient-error retries with backoff


def _retry(fn, retries: int = DEFAULT_RETRIES, base_delay: float = 0.75):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # vendor/transient errors — back off and retry
            last = exc
            if attempt == retries:
                break
            time.sleep(base_delay * (2 ** attempt))
    raise RuntimeError(f"model call failed after {retries + 1} attempts: {last}") from last


def _strip_markdown_fence(text: str) -> str:
    """Models often wrap a response in a ```json ... ``` (or bare ``` ... ```) fence even
    when the prompt says "no prose outside the JSON" — a well-documented quirk, not a
    provider bug. Left alone, the grader's json.loads() fails on the fence characters with
    "model_client must return valid JSON". Strip one leading/trailing fence if present;
    plain prose (no fence) passes through unchanged."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped
    body = stripped[first_newline + 1:].rstrip()
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


class AnthropicModelClient:
    """Adapter for Anthropic's Messages API."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = DEFAULT_TIMEOUT_S):
        import anthropic  # lazy
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                                            timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    def __call__(self, system: str, user: str, temperature: float = 0.7) -> str:
        def _do():
            resp = self._client.messages.create(
                model=self._model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=self._max_tokens,
                temperature=temperature,
            )
            # concatenate any text blocks
            text = "".join(getattr(b, "text", "") for b in resp.content).strip()
            return _strip_markdown_fence(text)
        return _retry(_do)


class OpenAIModelClient:
    """Adapter for OpenAI's Chat Completions API."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = DEFAULT_TIMEOUT_S):
        import openai  # lazy
        self._client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"),
                                     timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    def __call__(self, system: str, user: str, temperature: float = 0.7) -> str:
        def _do():
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=self._max_tokens,
                temperature=temperature,
            )
            return _strip_markdown_fence(resp.choices[0].message.content or "")
        return _retry(_do)


class OfflineModelClient:
    """Deterministic, no-network client for local dev and CI. NOT for production prose —
    it just renders the grounded direction the prompt already contains, so the app runs and
    the plumbing is exercised without a vendor key. (The backend's own mock fallbacks are similar;
    this lets you drive the live *code path* offline.)"""

    def __call__(self, system: str, user: str, temperature: float = 0.7) -> str:
        u = user.lower()
        # If the grader prompt asks for strict JSON, return a valid neutral JSON object.
        if "strict json" in system.lower() or "argument_coherence" in system:
            return ('{"argument_coherence": 60, "honest_evidence_citation": 60, '
                    '"ownership_of_tradeoffs": 60, "justification": "offline stub grade."}')
        speaker = system.split(",")[0].replace("You are ", "").strip() or "Someone"
        if "argue against" in u or "cool about" in u:
            tone = "I'm not sold on this"
        elif "argue for" in u or "warm about" in u:
            tone = "I'm into this"
        else:
            tone = "I'm on the fence"
        return f"{speaker}: {tone} — but it's your call."


def make_model_client(provider: Optional[str] = None, model: Optional[str] = None,
                      api_key: Optional[str] = None, **kwargs) -> ModelClient:
    """Factory. provider defaults to $FLEXEE_LLM_PROVIDER or 'offline'; model to $FLEXEE_LLM_MODEL."""
    provider = (provider or os.environ.get("FLEXEE_LLM_PROVIDER") or "offline").lower()
    model = model or os.environ.get("FLEXEE_LLM_MODEL")
    if provider == "anthropic":
        return AnthropicModelClient(model or "claude-3-5-sonnet-latest", api_key=api_key, **kwargs)
    if provider == "openai":
        return OpenAIModelClient(model or "gpt-4o", api_key=api_key, **kwargs)
    if provider == "offline":
        return OfflineModelClient()
    raise ValueError(f"unknown provider: {provider!r} (use anthropic | openai | offline)")


# --- Optional convenience wrappers ---------------------------------------------------------------

def json_judge_client(base: ModelClient) -> ModelClient:
    """The rubric harnesses accept a judge model. Judges must return strict JSON; run the judge at
    a low temperature for stability. This wraps any client to force temperature 0 for judging."""
    def judge(system: str, user: str, temperature: float = 0.0) -> str:
        return base(system, user, 0.0)
    return judge


if __name__ == "__main__":
    # offline smoke test — no network, no keys
    mc = make_model_client("offline")
    print(mc("You are Marcus, a loyal customer.", "React to the brand. Right now you feel warm about it.", 0.7))
    print(mc("You are Elena Vasquez, Brand Strategist.", "On the decision, argue AGAINST it, hard.", 0.7))
    print(mc('Return STRICT JSON with argument_coherence...', "STUDENT DEFENSE: ...", 0.0))
