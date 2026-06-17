"""Labeler implementations + a factory that picks one based on settings."""
from __future__ import annotations

from .base import Labeler, softmax
from .stub import KeywordStubLabeler, make_stub_ensemble
from .llm import LLMLabeler

__all__ = ["Labeler", "softmax", "KeywordStubLabeler", "make_stub_ensemble",
           "LLMLabeler", "make_labelers"]


def make_labelers(settings):
    """Return the labeler ensemble for the configured provider.

    Falls back to the deterministic stub ensemble when no API key is present, so
    the system always runs.
    """
    if settings.provider == "anthropic" and settings.anthropic_api_key:
        return [LLMLabeler("anthropic", settings.anthropic_api_key)]
    if settings.provider == "openai" and settings.openai_api_key:
        return [LLMLabeler("openai", settings.openai_api_key)]
    return make_stub_ensemble()
