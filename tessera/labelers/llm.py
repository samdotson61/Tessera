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


def letters_needed(labels) -> bool:
    """True when the word-style logprob head can't tell the labels apart by
    their first token: two labels sharing their first three characters
    (billing_dispute / billing_question) collapse to the same answer token,
    whose probability mass the prefix-matcher must then discard. Letter-keyed
    answers (A/B/C…) give every option its own unambiguous token. Capped at
    26 labels — beyond that, fall back to words."""
    if not labels or len(labels) > 26:
        return False
    heads = {str(l).lower()[:3] for l in labels}
    return len(heads) < len(labels)


class LLMLabeler(Labeler):
    ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
    OPENAI_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, provider: str = "anthropic", api_key: str = "",
                 model: str | None = None, model_id: str | None = None,
                 n_samples: int = 5, cache: ResponseCache | None = None,
                 max_retries: int = 3, transport=None, base_url: str = "",
                 examples=None, fewshot: int = 0, logprobs: bool = False,
                 fewshot_static: bool = False, answer_key: str = "auto"):
        self.provider = provider
        self.api_key = api_key
        # base_url points the provider-shaped request at any compatible
        # endpoint — winc.cpp/llama.cpp for the anthropic shape, ollama/vLLM
        # for the openai shape — for free, fully-local labeling. The API key
        # is optional there.
        self.base_url = base_url or (
            self.ANTHROPIC_URL if provider == "anthropic" else self.OPENAI_URL)
        self.model = model or ("claude-haiku-4-5" if provider == "anthropic" else "gpt-4o-mini")
        self.model_id = model_id or f"{provider}:{self.model}"
        self.n_samples = max(1, n_samples)
        self.cache = cache
        self.max_retries = max_retries
        self._transport = transport or self._http_call  # injectable for offline tests
        # Gold few-shot (docs/05 Phase A RAG-lite): the k nearest gold examples
        # (hashed-BoW cosine) are shown in the prompt. Classification only.
        self.fewshot = max(0, fewshot)
        # Logprob head (classification, openai-shaped local servers): one call
        # per item, the label token's top-logprobs ARE the distribution —
        # continuous confidence at 1/5th the calls of self-consistency voting.
        self.logprobs = bool(logprobs) and provider == "openai"
        self.answer_key = answer_key      # "auto" | "letter" | "word" (logprob mode)
        self._examples = []
        self._static = []
        if examples and self.fewshot:
            from ..engine.embed import embed
            self._examples = [(embed(t), t, lab) for t, lab in examples]
            if fewshot_static:
                # One fixed block, spread across classes round-robin: every
                # prompt shares its prefix, so the server's prefix cache pays
                # the example prefill once instead of per item.
                by_class = {}
                for t, lab in examples:
                    by_class.setdefault(lab, []).append(t)
                order = sorted(by_class)
                i = 0
                while len(self._static) < self.fewshot and any(by_class.values()):
                    lab = order[i % len(order)]
                    if by_class[lab]:
                        self._static.append((by_class[lab].pop(0), lab))
                    i += 1

    def _fewshot_block(self, item_text: str, letter_of=None) -> str:
        if not self._examples:
            return ""

        def answer(lab):
            if self.logprobs:
                return letter_of[lab] if letter_of else lab
            return f'{{"label": "{lab}"}}'

        if self._static and all(t != item_text for t, _ in self._static):
            # the shared block (an item appearing in it falls through to the
            # nearest-mode block below, which excludes self — no gold leak)
            lines = ["", "Examples of correct labels:"]
            lines += [f'Text: {t}\nAnswer: {answer(lab)}' for t, lab in self._static]
            return "\n".join(lines) + "\n"
        from ..engine.embed import embed, cosine
        v = embed(item_text)
        ranked = sorted(self._examples, key=lambda e: cosine(v, e[0]), reverse=True)
        lines = ["", "Examples of correct labels:"]
        n = 0
        for _, t, lab in ranked:
            if t == item_text:
                continue   # never leak the item's own gold label
            lines.append(f'Text: {t}\nAnswer: {answer(lab)}')
            n += 1
            if n >= self.fewshot:
                break
        return "\n".join(lines) + "\n" if n else ""

    def label(self, item: Item, taxonomy: Taxonomy) -> LabelOutput:
        span_mode = taxonomy.label_type == "span"
        if self.logprobs and taxonomy.label_type == "classification":
            mode = self.answer_key
            if mode == "auto":
                mode = "letter" if letters_needed(taxonomy.labels) else "word"
            if mode == "letter" and 0 < len(taxonomy.labels) <= 26:
                letter_of = {lab: chr(65 + i) for i, lab in enumerate(taxonomy.labels)}
                prompt = (taxonomy.to_prompt(style="letter") + "\n" +
                          self._fewshot_block(item.render(), letter_of=letter_of) +
                          "\nText:\n" + item.render() + "\nAnswer:")
                return self._label_logprob(prompt, taxonomy.labels, letter_of=letter_of)
            prompt = (taxonomy.to_prompt(style="word") + "\n" +
                      self._fewshot_block(item.render()) +
                      "\nText:\n" + item.render() + "\nAnswer:")
            return self._label_logprob(prompt, taxonomy.labels)
        fewshot = ("" if span_mode or taxonomy.label_type == "pairwise"
                   else self._fewshot_block(item.render()))
        prompt = taxonomy.to_prompt() + "\n" + fewshot + "\nText:\n" + item.render()
        labels = taxonomy.labels
        samples = []          # (label, conf, rationale) per successful sample
        last_err = None
        for s in range(self.n_samples):
            try:
                if span_mode:
                    label, conf, rationale = self._sample_span(prompt, s, item.render())
                else:
                    label, conf, rationale = self._sample(prompt, s)
                    if label not in labels and labels:
                        label = labels[0]
            except Exception as e:
                last_err = e
                continue
            samples.append((label, max(0.0, min(1.0, conf)), rationale))
        if not samples:  # fail soft -> low-information output, gets routed
            if span_mode:
                return LabelOutput(self.model_id, {}, f"LLM error: {last_err}")
            uniform = {l: 1.0 / len(labels) for l in labels} if labels else {}
            return LabelOutput(self.model_id, uniform, f"LLM error: {last_err}")

        # Average the per-sample distributions: vote agreement and verbalized
        # confidence both shape the result (5 confident agreeing samples -> sharp;
        # split votes or hedged confidences -> flat -> routed to a human).
        # Span mode votes over whole annotations (each sample's canonical
        # span-set is one candidate) — a boundary or type disagreement between
        # samples flattens the distribution exactly like a label disagreement.
        if span_mode:
            mean = {}
            for ann, conf, _ in samples:
                mean[ann] = mean.get(ann, 0.0) + conf / len(samples)
        else:
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

    def _label_logprob(self, prompt: str, labels: list, letter_of=None) -> LabelOutput:
        """One call; the first answer token's top-logprobs become the label
        distribution. In word mode, tokens are matched to labels by unambiguous
        prefix (a token claiming several labels is dropped); in letter mode
        (letter_of: label -> "A"/"B"/…) a token must be exactly the option's
        letter. Unmatched probability mass is discarded and the rest
        renormalized — mass that went to non-label tokens is real uncertainty
        and should flatten the result."""
        import math as _math
        key = ResponseCache.key(self.provider, self.model, "lp-v1", prompt, 0)
        raw = self.cache.get(key) if self.cache else None
        if raw is None:
            try:
                raw = self._call_with_retries(prompt)
            except Exception as e:
                uniform = {l: 1.0 / len(labels) for l in labels} if labels else {}
                return LabelOutput(self.model_id, uniform, f"LLM error: {e}")
            if self.cache is not None:
                self.cache.put(key, raw)
        try:
            body = json.loads(raw)
            tops = body["top_logprobs"]
            answer = str(body.get("content", "")).strip()
        except (ValueError, KeyError, TypeError):
            uniform = {l: 1.0 / len(labels) for l in labels} if labels else {}
            return LabelOutput(self.model_id, uniform, "logprobs unavailable in reply")
        dist = {l: 0.0 for l in labels}
        by_letter = ({v: k for k, v in letter_of.items()} if letter_of else None)
        for entry in tops:
            tok = str(entry.get("token", "")).strip()
            if not tok:
                continue
            if by_letter is not None:
                lab = by_letter.get(tok.rstrip(".):").upper())
                if lab is not None:
                    dist[lab] += _math.exp(float(entry["logprob"]))
                continue
            matches = [l for l in labels if l.lower().startswith(tok.lower())]
            if len(matches) == 1:
                dist[matches[0]] += _math.exp(float(entry["logprob"]))
        z = sum(dist.values())
        if z <= 0:   # nothing matched a label: route it
            dist = {l: 1.0 / len(labels) for l in labels}
            return LabelOutput(self.model_id, dist,
                               f"no label mass in logprobs (answer {answer!r})")
        dist = {l: v / z for l, v in dist.items()}
        best = max(dist, key=dist.get)
        return LabelOutput(self.model_id, dist,
                           f"logprob head: P({best})={dist[best]:.3f}, "
                           f"label mass {z:.3f}")

    def _sample_span(self, prompt: str, sample_idx: int, text: str):
        """One completion for a span item -> (canonical annotation, conf, rationale).

        The prompt asks for exact quotes (models are unreliable with character
        offsets); quotes are resolved to offsets here. An unresolvable quote
        fails the sample — fail-soft routing beats shipping a guessed offset.
        """
        from ..engine import spans as spans_mod
        key = ResponseCache.key(self.provider, self.model, "span-v1", prompt, sample_idx)
        raw = self.cache.get(key) if self.cache else None
        if raw is None:
            raw = self._call_with_retries(prompt)
            if self.cache is not None:
                self.cache.put(key, raw)
        obj = json.loads(_extract_json(raw))
        quoted = obj.get("spans")
        if not isinstance(quoted, list):
            raise ValueError("LLM response missing 'spans' list")
        spans, unresolved = spans_mod.resolve_quoted(quoted, text)
        if unresolved:
            raise ValueError(f"unresolvable span quote(s): {unresolved[:3]}")
        return (spans_mod.canonical(spans), float(obj.get("confidence", 0.7)),
                str(obj.get("rationale", "")))

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
            # generous timeout: local servers decode far slower than APIs and
            # queue concurrent requests
            with urllib.request.urlopen(req, timeout=180) as r:
                body = json.loads(r.read())
            return body["content"][0]["text"]
        payload = {"model": self.model, "max_tokens": 256,
                   "messages": [{"role": "user", "content": prompt}]}
        if self.logprobs:
            payload["max_tokens"] = 5
            payload["logprobs"] = True
            payload["top_logprobs"] = 20
            # llama-server honors per-request KV prompt caching on the OpenAI
            # endpoint only when asked: with the shared taxonomy prefix this
            # cuts prefill to roughly the item's own tokens. Servers that
            # don't know the field ignore it.
            payload["cache_prompt"] = True
        req = urllib.request.Request(
            self.base_url, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key or 'local'}",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            body = json.loads(r.read())
        choice = body["choices"][0]
        if self.logprobs:
            content_lp = (choice.get("logprobs") or {}).get("content") or []
            tops = content_lp[0].get("top_logprobs", []) if content_lp else []
            return json.dumps({
                "content": choice["message"].get("content", ""),
                "top_logprobs": [{"token": t.get("token", ""),
                                  "logprob": t.get("logprob", -100.0)} for t in tops]})
        return choice["message"]["content"]
