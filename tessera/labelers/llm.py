"""Real labeler via Anthropic or OpenAI, using only stdlib urllib.

Confidence follows docs/04 Layer 1: each item is sampled N times
(**self-consistency**) and every sample's verbalized confidence is spread over
the label set; the per-sample distributions are averaged, so the final
distribution blends vote agreement with verbalized confidence. Responses are
cached on (provider, model, params, prompt, sample) — see cache.py — and calls
retry with exponential backoff on 429/5xx/network errors.

Used when an API key is configured; otherwise the CLI falls back to the
deterministic stub. Not exercised by the offline test suite over the network —
tests inject a fake transport.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .base import Labeler
from .cache import ResponseCache
from ..schemas import Item, Taxonomy, LabelOutput

RETRIABLE_STATUS = {429, 500, 502, 503, 529}


def _extract_json(text: str) -> str:
    """Return the first balanced {...} object in text (models often wrap JSON in prose)."""
    text = text or ""
    start = text.find("{")
    if start < 0:
        return "{}"
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return "{}"


def _salvage(text: str):
    """Regex fallback for near-JSON replies (small local models drop a quote or
    brace now and then). Recovers label + confidence when those fields are
    intact; raises if even the label is missing."""
    m = re.search(r'"label"\s*:\s*"([^"]+)"', text)
    if not m:
        raise ValueError("no parsable 'label' in LLM response")
    c = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', text)
    r = re.search(r'"rationale"\s*:\s*"?\s*(.+?)"?\s*}?\s*$', text, re.S)
    return m.group(1), float(c.group(1)) if c else 0.7, (r.group(1).strip() if r else "")


def _spread(label: str, conf: float, labels: list) -> dict:
    """Distribution putting conf on label, the remainder uniform over the rest."""
    if len(labels) <= 1:
        return {label: 1.0}
    other = (1.0 - conf) / (len(labels) - 1)
    dist = {l: other for l in labels}
    dist[label] = conf
    return dist


class LLMLabeler(Labeler):
    ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, provider: str = "anthropic", api_key: str = "",
                 model: str | None = None, model_id: str | None = None,
                 n_samples: int = 5, cache: ResponseCache | None = None,
                 max_retries: int = 3, transport=None, base_url: str = ""):
        self.provider = provider
        self.api_key = api_key
        # base_url points the anthropic-shaped request at any /v1/messages
        # endpoint — e.g. a local winc.cpp / llama.cpp server — for free,
        # fully-local labeling. The API key is optional there.
        self.base_url = base_url or self.ANTHROPIC_URL
        self.model = model or ("claude-haiku-4-5" if provider == "anthropic" else "gpt-4o-mini")
        self.model_id = model_id or f"{provider}:{self.model}"
        self.n_samples = max(1, n_samples)
        self.cache = cache
        self.max_retries = max_retries
        self._transport = transport or self._http_call  # injectable for offline tests

    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        prompt = taxonomy.to_prompt() + "\n\nText:\n" + item.render()
        labels = taxonomy.labels
        samples = []          # (label, conf, rationale) per successful sample
        last_err = None
        for s in range(self.n_samples):
            try:
                label, conf, rationale = self._sample(prompt, s)
            except Exception as e:
                last_err = e
                continue
            if label not in labels and labels:
                label = labels[0]
            samples.append((label, max(0.0, min(1.0, conf)), rationale))
        if not samples:  # fail soft -> uniform, low-confidence, gets routed
            uniform = {l: 1.0 / len(labels) for l in labels} if labels else {}
            return LabelOutput(self.model_id, uniform, f"LLM error: {last_err}")

        # Average the per-sample distributions: vote agreement and verbalized
        # confidence both shape the result (5 confident agreeing samples -> sharp;
        # split votes or hedged confidences -> flat -> routed to a human).
        mean = {l: 0.0 for l in labels} if labels else {}
        for label, conf, _ in samples:
            for l, v in _spread(label, conf, labels).items():
                mean[l] = mean.get(l, 0.0) + v / len(samples)
        z = sum(mean.values()) or 1.0
        mean = {l: v / z for l, v in mean.items()}
        winner = max(mean, key=mean.get)
        votes = sum(1 for lab, _, _ in samples if lab == winner)
        rationale = next((r for lab, _, r in samples if lab == winner and r), "")
        if len(samples) > 1:
            rationale = f"[{votes}/{len(samples)} samples agree] {rationale}"
        return LabelOutput(self.model_id, mean, rationale)

    def _sample(self, prompt: str, sample_idx: int):
        """One (possibly cached) completion -> (label, confidence, rationale)."""
        key = ResponseCache.key(self.provider, self.model, "v1", prompt, sample_idx)
        text = self.cache.get(key) if self.cache else None
        if text is None:
            text = self._call_with_retries(prompt)
            if self.cache is not None:
                self.cache.put(key, text)
        try:
            obj = json.loads(_extract_json(text))
        except json.JSONDecodeError:
            return _salvage(text)
        label = obj.get("label")
        if not label:
            raise ValueError("LLM response missing 'label'")
        return str(label), float(obj.get("confidence", 0.7)), str(obj.get("rationale", ""))

    def _call_with_retries(self, prompt: str) -> str:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                return self._transport(prompt)
            except urllib.error.HTTPError as e:
                if e.code not in RETRIABLE_STATUS or attempt == self.max_retries:
                    raise
            except urllib.error.URLError:
                if attempt == self.max_retries:
                    raise
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("unreachable")

    def _http_call(self, prompt: str) -> str:
        # No temperature is sent: provider defaults give the sampling diversity
        # self-consistency needs, and newer Anthropic models reject the parameter.
        if self.provider == "anthropic":
            req = urllib.request.Request(
                self.base_url,
                data=json.dumps({
                    "model": self.model, "max_tokens": 256,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={"x-api-key": self.api_key or "local",
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read())
            return body["content"][0]["text"]
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read())
        return body["choices"][0]["message"]["content"]
