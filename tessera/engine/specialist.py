"""Tier-0 specialist: a tiny trained classifier for the labeling cascade.

Multinomial logistic regression over hashed bag-of-words features — pure
stdlib, trains on a few hundred finalized labels in well under a second, and
labels ~10,000+ items/second on any laptop. It is the first tier of the
organization-scale cascade (docs/05 Phase B lite): the specialist handles the
easy mass at its own calibrated gate, the local LLM handles what it can't,
humans handle the residue — and every correction retrains the specialist.

Train ONLY on human-trusted labels (gold + human-reviewed finals). Training on
auto-applied LLM labels bakes the LLM's errors into the tier that is supposed
to be checked by it (docs/05 §9's poisoning warning).
"""
from __future__ import annotations

import hashlib
import math
import random

from .embed import embed
from ..schemas import Item, Taxonomy, LabelOutput
from ..labelers.base import Labeler


class Specialist:
    def __init__(self, labels, dim: int = 1024, epochs: int = 40,
                 lr: float = 0.5, l2: float = 1e-4, seed: int = 0):
        self.labels = list(labels)
        self.dim = dim
        self.epochs = epochs
        self.lr = lr
        self.l2 = l2
        self.seed = seed
        self.w = {lab: {} for lab in self.labels}   # sparse weights per class
        self.b = {lab: 0.0 for lab in self.labels}

    def _scores(self, vec: dict) -> dict:
        return {lab: self.b[lab] + sum(v * self.w[lab].get(k, 0.0)
                                       for k, v in vec.items())
                for lab in self.labels}

    def _softmax(self, scores: dict) -> dict:
        m = max(scores.values())
        exp = {lab: math.exp(s - m) for lab, s in scores.items()}
        z = sum(exp.values()) or 1.0
        return {lab: e / z for lab, e in exp.items()}

    def train(self, texts, labels):
        data = [(embed(t, self.dim), lab) for t, lab in zip(texts, labels)]
        rng = random.Random(self.seed)
        for _ in range(self.epochs):
            rng.shuffle(data)
            for vec, gold in data:
                probs = self._softmax(self._scores(vec))
                for lab in self.labels:
                    g = probs[lab] - (1.0 if lab == gold else 0.0)
                    if g == 0.0:
                        continue
                    wl = self.w[lab]
                    for k, v in vec.items():
                        wl[k] = wl.get(k, 0.0) * (1.0 - self.lr * self.l2) - self.lr * g * v
                    self.b[lab] -= self.lr * g
        return self

    def predict(self, text: str) -> dict:
        return self._softmax(self._scores(embed(text, self.dim)))


class SpecialistLabeler(Labeler):
    """The trained specialist as an ordinary labeler: its probability
    distribution flows through the same calibration + gate as any LLM's."""
    def __init__(self, specialist: Specialist, model_id: str = "specialist-logreg-v1"):
        self.specialist = specialist
        self.model_id = model_id

    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        dist = self.specialist.predict(item.render())
        best = max(dist, key=dist.get)
        return LabelOutput(self.model_id, dist,
                           f"specialist head: P({best})={dist[best]:.3f}")


def train_from_storage(storage, dataset_id, taxonomy) -> Specialist:
    """Train on the human-trusted corpus: gold plus human-reviewed finals."""
    gold = storage.get_gold(dataset_id)
    items = {it.id: it for it in storage.get_items(dataset_id)}
    texts, labels = [], []
    for iid, lab in gold.items():
        if iid in items:
            texts.append(items[iid].render())
            labels.append(lab)
    human = {e.item_id: e.final_label for e in storage.get_events(dataset_id)
             if e.routed_to_human and e.final_label is not None}
    for iid, lab in human.items():
        if iid in items and iid not in gold:
            texts.append(items[iid].render())
            labels.append(lab)
    return Specialist(taxonomy.labels).train(texts, labels)


def consensus_split(dataset_id: str, item_id: str) -> str:
    """Hash-stable half split of trusted labels: 'train' or 'calib'.

    The consensus specialist may only train on the 'train' half; calibration
    may only use the 'calib' half. Training memorization otherwise masquerades
    as ensemble agreement on exactly the items the calibrator learns from
    (measured on the cascade: same-gold calibration waved everything through)."""
    h = hashlib.sha256(f"spec-split|{dataset_id}|{item_id}".encode()).digest()
    return "train" if int.from_bytes(h[:8], "big") % 2 == 0 else "calib"


def train_consensus(storage, dataset_id, taxonomy, min_train: int = 10,
                    min_calib: int = 10):
    """Train the consensus specialist on the TRAIN half of the trusted corpus.

    Returns (Specialist, n_train) or (None, n_train) when the trade is bad:
    fewer than min_train training examples, fewer than 2 distinct labels, or —
    the default-on safety rule — fewer than min_calib GOLD items left on the
    calibration half. The leak guard halves the calibration gold; if the
    remainder cannot support honest cross-validated calibration, the co-signal
    is not worth the CV it would cost, and the run proceeds unchanged.
    Classification only — the hashed-BoW head has no notion of spans or of
    A/B response positions."""
    if taxonomy.label_type != "classification":
        return None, 0
    gold = storage.get_gold(dataset_id)
    items = {it.id: it for it in storage.get_items(dataset_id)}
    n_calib_gold = sum(1 for iid in gold
                       if iid in items and consensus_split(dataset_id, iid) == "calib")
    trusted = dict(gold)
    for e in storage.get_events(dataset_id):
        if e.routed_to_human and e.final_label is not None and e.item_id not in gold:
            trusted[e.item_id] = e.final_label
    texts, labels = [], []
    for iid, lab in sorted(trusted.items()):
        if iid in items and consensus_split(dataset_id, iid) == "train":
            texts.append(items[iid].render())
            labels.append(lab)
    if len(texts) < min_train or len(set(labels)) < 2 or n_calib_gold < min_calib:
        return None, len(texts)
    return Specialist(taxonomy.labels).train(texts, labels), len(texts)
