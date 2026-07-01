"""LLM-as-judge verification pass (docs/04, Layer 3).

A second model reviews each auto-apply candidate and can veto it back to the
human queue. To avoid correlated errors the judge should be a *different model
family* than the labeler (set TESSERA_JUDGE=openai when labeling with
anthropic, or vice versa). The judge only ever narrows the auto-applied set —
a veto routes the item to a human — so it can raise precision but never
auto-apply something the calibrated gate rejected. If the judge errors, the
gate's decision stands (deterministic checks remain the trustworthy floor).
"""
from __future__ import annotations

import json

from .cache import ResponseCache, open_cache
from .llm import LLMLabeler, _extract_json
from ..schemas import Item, Taxonomy


def _judge_prompt(item: Item, taxonomy: Taxonomy, label: str) -> str:
    lines = [
        "You are a strict reviewer checking a proposed data label.",
        f"Guidelines: {taxonomy.guidelines}".rstrip(),
        "Labels:",
    ]
    for lab in taxonomy.labels:
        d = taxonomy.definitions.get(lab, "")
        lines.append(f"- {lab}: {d}" if d else f"- {lab}")
    lines.append("\nText:\n" + item.render())
    lines.append(f"\nProposed label: {label}")
    lines.append('Does the proposed label comply with the guidelines? '
                 'Respond ONLY as JSON: {"verdict": "pass" or "fail", "reason": <short>}')
    return "\n".join(lines)


class LLMJudge:
    def __init__(self, provider: str = "openai", api_key: str = "",
                 model: str | None = None, cache: ResponseCache | None = None,
                 max_retries: int = 3, transport=None):
        # Reuse the labeler's transport/retry plumbing; the judge is one sample.
        self._llm = LLMLabeler(provider, api_key, model=model, n_samples=1,
                               cache=cache, max_retries=max_retries, transport=transport)
        self.provider = provider
        self.model_id = f"judge:{self._llm.model_id}"

    def review(self, item: Item, taxonomy: Taxonomy, label: str):
        """Return (ok, reason). ok=False vetoes the auto-apply. Errors fail open."""
        prompt = _judge_prompt(item, taxonomy, label)
        try:
            key = ResponseCache.key(self.provider, self._llm.model, "judge-v1", prompt)
            text = self._llm.cache.get(key) if self._llm.cache else None
            if text is None:
                text = self._llm._call_with_retries(prompt)
                if self._llm.cache is not None:
                    self._llm.cache.put(key, text)
            obj = json.loads(_extract_json(text))
        except Exception as e:
            return (True, f"judge unavailable ({e}); gate decision stands")
        if str(obj.get("verdict", "pass")).lower() == "fail":
            return (False, str(obj.get("reason", "judge failed the label")))
        return (True, str(obj.get("reason", "")))


def make_judge(settings):
    """Judge from settings, or None when TESSERA_JUDGE is unset."""
    provider = getattr(settings, "judge_provider", "")
    if not provider:
        return None
    key = {"anthropic": settings.anthropic_api_key,
           "openai": settings.openai_api_key}.get(provider, "")
    if not key:
        return None
    if provider == settings.provider:
        # Allowed, but a same-family judge shares the labeler's blind spots.
        pass
    return LLMJudge(provider, key, model=settings.judge_model or None,
                    cache=open_cache(settings.cache_path))
