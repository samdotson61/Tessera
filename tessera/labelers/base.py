"""Labeler interface + a numerically-stable softmax."""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from ..schemas import Item, Taxonomy, LabelOutput


def softmax(scores: dict, temperature: float = 1.0) -> dict:
    """Convert raw scores to a probability distribution. Higher temperature = flatter."""
    if not scores:
        return {}
    t = max(temperature, 1e-6)
    scaled = {k: v / t for k, v in scores.items()}
    m = max(scaled.values())
    exp = {k: math.exp(v - m) for k, v in scaled.items()}
    z = sum(exp.values()) or 1.0
    return {k: v / z for k, v in exp.items()}


class Labeler(ABC):
    """Produces a probability distribution over a taxonomy's labels for one item."""
    model_id: str = "labeler"

    @abstractmethod
    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        ...
