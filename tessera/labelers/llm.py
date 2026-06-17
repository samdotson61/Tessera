"""Optional real labeler via Anthropic or OpenAI, using only stdlib urllib.

Used when an API key is configured; otherwise the CLI falls back to the
deterministic stub. Not exercised by the offline test suite (no network).
"""
from __future__ import annotations

import json
import re
import urllib.request

from .base import Labeler
from ..schemas import Item, Taxonomy, LabelOutput

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> str:
    m = _JSON.search(text or "")
    return m.group(0) if m else "{}"


class LLMLabeler(Labeler):
    def __init__(self, provider: str = "anthropic", api_key: str = "",
                 model: str | None = None, model_id: str | None = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model or ("claude-3-5-haiku-latest" if provider == "anthropic" else "gpt-4o-mini")
        self.model_id = model_id or f"{provider}:{self.model}"

    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        prompt = taxonomy.to_prompt() + "\n\nText:\n" + item.text
        labels = taxonomy.labels
        try:
            label, conf, rationale = self._call(prompt)
        except Exception as e:  # fail soft -> uniform, low-confidence, gets routed
            uniform = {l: 1.0 / len(labels) for l in labels} if labels else {}
            return LabelOutput(self.model_id, uniform, f"LLM error: {e}")
        if label not in labels:
            label = labels[0] if labels else label
        conf = max(0.0, min(1.0, conf))
        if len(labels) > 1:
            other = (1.0 - conf) / (len(labels) - 1)
            dist = {l: other for l in labels}
            dist[label] = conf
        else:
            dist = {label: 1.0}
        return LabelOutput(self.model_id, dist, rationale)

    def _call(self, prompt: str):
        if self.provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps({
                    "model": self.model, "max_tokens": 256,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
            text = body["content"][0]["text"]
        else:
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps({
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={"Authorization": f"Bearer {self.api_key}",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
            text = body["choices"][0]["message"]["content"]
        obj = json.loads(_extract_json(text))
        return obj["label"], float(obj.get("confidence", 0.7)), obj.get("rationale", "")
