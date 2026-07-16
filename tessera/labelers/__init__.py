"""Labeler implementations + a factory that picks one based on settings."""
from __future__ import annotations

from .base import Labeler, softmax
from .stub import KeywordStubLabeler, make_stub_ensemble
from .llm import LLMLabeler
from .cache import ResponseCache, open_cache

__all__ = ["Labeler", "softmax", "KeywordStubLabeler", "make_stub_ensemble",
           "LLMLabeler", "ResponseCache", "open_cache", "make_labelers"]


def make_labelers(settings):
    """Return the labeler ensemble for the configured provider(s).

    TESSERA_PROVIDER accepts a comma list ("anthropic,openai") for the
    two-family ensemble docs/04 recommends — ensemble disagreement across model
    families is the strongest uncertainty signal. Providers without a key are
    skipped; with none usable, falls back to the deterministic stub ensemble so
    the system always runs.
    """
    cache = None   # opened lazily so keyless/stub runs never create a cache file
    labelers = []
    model = settings.model_id if settings.model_id != "stub-kw-v1" else None
    for provider in [p.strip() for p in settings.provider.split(",") if p.strip()]:
        key = {"anthropic": settings.anthropic_api_key,
               "openai": settings.openai_api_key}.get(provider, "")
        # A provider pointed at an alternate URL (TESSERA_ANTHROPIC_URL /
        # TESSERA_OPENAI_URL, e.g. winc.cpp or ollama) needs no key —
        # fully-local labeling is free.
        url = {"anthropic": settings.anthropic_url,
               "openai": settings.openai_url}.get(provider, "")
        if key or url:
            if cache is None:
                cache = open_cache(settings.cache_path)
            labelers.append(LLMLabeler(
                provider, key, model=model, n_samples=settings.llm_samples,
                cache=cache, base_url=url))
    return labelers or make_stub_ensemble()
