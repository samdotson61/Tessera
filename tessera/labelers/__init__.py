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
    cache = open_cache(settings.cache_path)
    labelers = []
    for provider in [p.strip() for p in settings.provider.split(",") if p.strip()]:
        key = {"anthropic": settings.anthropic_api_key,
               "openai": settings.openai_api_key}.get(provider, "")
        if key:
            labelers.append(LLMLabeler(provider, key, n_samples=settings.llm_samples,
                                       cache=cache))
    return labelers or make_stub_ensemble()
