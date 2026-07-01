"""Deterministic, offline labeler used so the whole loop runs without any API key.

It scores each label by keyword overlap between the item text and the label's
name + definition, then softmaxes to a distribution. A small hash-based jitter
(seeded per model_id) lets two ensemble members disagree on genuine near-ties,
which makes the ensemble-disagreement signal meaningful — while staying fully
deterministic and reproducible. Replace with an LLM labeler (llm.py) for real
accuracy; this exists purely to make the MVP runnable and the tests stable.
"""
from __future__ import annotations

import hashlib
import re

from .base import Labeler, softmax
from ..schemas import Item, Taxonomy, LabelOutput

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "to", "of", "and", "or", "for", "is", "it", "this", "that",
    "with", "on", "in", "you", "your", "my", "i", "we", "be", "are", "can", "not",
    "have", "has", "do", "does", "please", "would", "could", "want", "need",
}


def _tokens(text: str) -> list:
    return _WORD.findall(text.lower())


def _keywords_for(taxonomy: Taxonomy) -> dict:
    """Keyword set per label, derived from the label name + its definition words."""
    kw = {}
    for lab in taxonomy.labels:
        words = set(_tokens(lab.replace("_", " ")))
        words |= {w for w in _tokens(taxonomy.definitions.get(lab, ""))
                  if len(w) > 2 and w not in _STOP}
        kw[lab] = words
    return kw


def _jitter(model_id: str, item_id: str, label: str, scale: float) -> float:
    if scale <= 0:
        return 0.0
    h = hashlib.sha1(f"{model_id}|{item_id}|{label}".encode()).digest()
    return (h[0] / 255.0) * scale


class KeywordStubLabeler(Labeler):
    def __init__(self, temperature: float = 0.5, jitter: float = 0.0,
                 model_id: str = "stub-kw-v1"):
        self.temperature = temperature
        self.jitter = jitter
        self.model_id = model_id

    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        if taxonomy.label_type == "pairwise" and item.is_pairwise():
            return self._label_pairwise(item, taxonomy)
        kw = _keywords_for(taxonomy)
        toks = set(_tokens(item.text))
        scores = {}
        matched = {}
        for lab in taxonomy.labels:
            hits = toks & kw[lab]
            scores[lab] = float(len(hits)) + 0.1 + _jitter(self.model_id, item.id, lab, self.jitter)
            matched[lab] = hits
        dist = softmax(scores, self.temperature)
        best = max(dist, key=dist.get) if dist else None
        hit_words = ", ".join(sorted(matched.get(best, []))) if best else ""
        rationale = f"matched: {hit_words}" if hit_words else "no strong keywords (ambiguous)"
        return LabelOutput(model_id=self.model_id, distribution=dist, rationale=rationale)

    def _label_pairwise(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        """Score each response by keyword overlap with the guidelines; the labels
        "A"/"B" get their response's score, anything else (e.g. "tie") scores near
        the loser. Same softmax + jitter as classification."""
        kw = {w for w in _tokens(taxonomy.guidelines) if len(w) > 2 and w not in _STOP}
        sides = {"A": len(set(_tokens(str(item.meta["response_a"]))) & kw),
                 "B": len(set(_tokens(str(item.meta["response_b"]))) & kw)}
        scores = {}
        for lab in taxonomy.labels:
            base = float(sides.get(lab, min(sides.values()) * 0.5))
            scores[lab] = base + 0.1 + _jitter(self.model_id, item.id, lab, self.jitter)
        dist = softmax(scores, self.temperature)
        best = max(dist, key=dist.get) if dist else None
        rationale = (f"guideline-keyword score A={sides['A']} B={sides['B']}"
                     if best in ("A", "B") else "responses score alike (ambiguous)")
        return LabelOutput(model_id=self.model_id, distribution=dist, rationale=rationale)


def make_stub_ensemble():
    """Two diverse stub configs so ensemble disagreement is non-trivial on near-ties."""
    return [
        KeywordStubLabeler(temperature=0.5, jitter=0.5, model_id="stub-kw-a"),
        KeywordStubLabeler(temperature=0.9, jitter=0.5, model_id="stub-kw-b"),
    ]
