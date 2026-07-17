"""Core data schemas (stdlib dataclasses).

Mirrors the entities in docs/06-data-model-and-api-contracts.md, kept lightweight
(no pydantic) so the MVP runs on the standard library alone.
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LabelType(str, Enum):
    CLASSIFICATION = "classification"   # single-label (MVP focus)
    SPAN = "span"                       # NER / span highlighting (future)
    PAIRWISE = "pairwise"               # A-vs-B preference


class HumanAction(str, Enum):
    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


@dataclass
class Dataset:
    id: str
    name: str = ""
    created_at: str = field(default_factory=now_iso)


@dataclass
class Item:
    id: str
    dataset_id: str
    text: str
    meta: dict = field(default_factory=dict)
    final_label: Optional[str] = None

    def is_pairwise(self) -> bool:
        return "response_a" in self.meta and "response_b" in self.meta

    def render(self) -> str:
        """The text a labeler sees. Pairwise items carry the two candidate
        responses in meta; text holds the prompt they answer."""
        if self.is_pairwise():
            parts = []
            if self.text:
                parts.append("Prompt:\n" + self.text)
            parts.append("Response A:\n" + str(self.meta["response_a"]))
            parts.append("Response B:\n" + str(self.meta["response_b"]))
            return "\n\n".join(parts)
        return self.text


@dataclass
class Taxonomy:
    id: str
    name: str
    version: int = 1
    label_type: str = LabelType.CLASSIFICATION.value
    labels: list = field(default_factory=list)
    definitions: dict = field(default_factory=dict)   # label -> definition text
    guidelines: str = ""

    def allowed_labels(self) -> list:
        return list(self.labels)

    def to_prompt(self, style: str = "json") -> str:
        if style == "word" and self.label_type == LabelType.CLASSIFICATION.value:
            # Single-word answer for logprob-head labeling: the label's first
            # token carries the whole distribution.
            lines = ["Task: assign exactly one label to the text below.",
                     f"Guidelines: {self.guidelines}".rstrip(), "Labels:"]
            for lab in self.labels:
                d = self.definitions.get(lab, "")
                lines.append(f"- {lab}: {d}" if d else f"- {lab}")
            lines.append("Respond with ONLY the label word, nothing else.")
            return "\n".join(lines)
        if self.label_type == LabelType.SPAN.value:
            lines = [
                "Task: extract every entity span from the text below.",
                f"Guidelines: {self.guidelines}".rstrip(),
                "Entity types:",
            ]
            for lab in self.labels:
                d = self.definitions.get(lab, "")
                lines.append(f"- {lab}: {d}" if d else f"- {lab}")
            lines.append('For each entity quote its EXACT text as it appears. Respond ONLY as '
                         'JSON: {"spans": [{"text": <exact quote>, "type": <one type>}, ...], '
                         '"confidence": <0..1>, "rationale": <short>}. '
                         'Use an empty spans list when the text contains no entities.')
            return "\n".join(lines)
        if self.label_type == LabelType.PAIRWISE.value:
            task = ("Task: compare the two candidate responses (A and B) to the prompt "
                    "below and pick the better one according to the guidelines.")
        else:
            task = "Task: assign exactly one label to the text below."
        lines = [task, f"Guidelines: {self.guidelines}".rstrip(), "Labels:"]
        for lab in self.labels:
            d = self.definitions.get(lab, "")
            lines.append(f"- {lab}: {d}" if d else f"- {lab}")
        lines.append('Respond ONLY as JSON: {"label": <one label>, "confidence": <0..1>, "rationale": <short>}')
        return "\n".join(lines)


@dataclass
class LabelOutput:
    """One model's output for one item: a probability distribution over labels."""
    model_id: str
    distribution: dict = field(default_factory=dict)  # label -> prob (sums ~1)
    rationale: str = ""

    def top(self):
        if not self.distribution:
            return (None, 0.0)
        lab = max(self.distribution, key=self.distribution.get)
        return (lab, self.distribution[lab])


@dataclass
class Prediction:
    item_id: str
    dataset_id: str
    taxonomy_id: str
    label: str
    confidence_raw: float
    confidence_calibrated: Optional[float] = None
    agreement: float = 1.0
    rationale: str = ""
    votes: dict = field(default_factory=dict)         # model_id -> label
    distribution: dict = field(default_factory=dict)  # ensemble label -> prob
    auto_applied: Optional[bool] = None
    routed: Optional[bool] = None
    audit: bool = False    # auto-applied AND selected for human audit (label ships, human verifies)
    source: str = ""

    def confidence(self) -> float:
        """Calibrated confidence if available, else raw."""
        return self.confidence_calibrated if self.confidence_calibrated is not None else self.confidence_raw


@dataclass
class GoldItem:
    item_id: str
    dataset_id: str
    label: str
    source: str = "seed"    # "seed" (curated) | "human" (grown from review decisions)


@dataclass
class Event:
    """Canonical flywheel event — see docs/06. Every interaction becomes one row."""
    item_id: str
    dataset_id: str
    model_id: str = ""
    model_label: str = ""
    model_rationale: str = ""
    confidence_raw: float = 0.0
    confidence_calibrated: Optional[float] = None
    ensemble_votes: dict = field(default_factory=dict)
    weak_supervision_votes: dict = field(default_factory=dict)
    routed_to_human: bool = False
    route_reason: str = ""
    human_action: Optional[str] = None
    final_label: Optional[str] = None
    human_rationale: Optional[str] = None
    taxonomy_version: int = 1
    rubric_snapshot: str = ""
    modality: str = "text"
    input_ref: str = ""
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    annotator_id: str = ""
    timestamp: str = field(default_factory=now_iso)
    id: Optional[int] = None    # storage row id, populated on read (for undo)


@dataclass
class GateResult:
    target_precision: float
    threshold: float
    coverage: float
    achieved_precision: float
    n_auto: int
    n_queue: int
    n_gold: int
    ece_before: float = 0.0
    ece_after: float = 0.0
    cross_validated: bool = False   # True if achieved/ece are out-of-sample (CV)
    n_judge_vetoed: int = 0         # auto-apply candidates the LLM judge routed to a human
    n_audit_pending: int = 0        # auto-applied items awaiting human audit
    coverage_ci: Optional[list] = None   # [lo, hi] bootstrap 95% CI on gold coverage


@dataclass
class QualityReport:
    dataset_id: str
    taxonomy_version: int
    target_precision: float
    threshold: float
    coverage: float
    achieved_precision: float
    per_label_precision: dict = field(default_factory=dict)
    n_items: int = 0
    n_auto: int = 0
    n_queue: int = 0
    n_gold: int = 0
    ece: float = 0.0
    coverage_ci: Optional[list] = None    # [lo, hi] bootstrap 95% CI on gold coverage
    reliability_bins: list = field(default_factory=list)  # calibrated conf vs accuracy per bin
    n_audit_pending: int = 0              # auto-applied items awaiting audit review
    n_audited: int = 0                    # audit reviews completed
    audit_precision: Optional[float] = None   # share of audited labels the human confirmed
    caveats: list = field(default_factory=list)
    generated_at: str = field(default_factory=now_iso)


def to_dict(obj) -> dict:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"not a dataclass instance: {type(obj)}")


def to_json(obj, indent=None) -> str:
    return json.dumps(to_dict(obj), indent=indent, default=str)
