"""Client LLM agnostik OpenAI-compatible (DeepSeek / router / vLLM / LiteLLM, dll).

Provider hanya ditentukan oleh config.yaml: base_url, api_key, model.
Fitur:
- retry + timeout agar tidak menggantung keputusan bar
- json_mode otomatis (skip response_format bila provider tidak mendukung)
- parse JSON robust (strip markdown fence, ambil blok {} pertama)
- validasi ketat schema keputusan; output tidak valid => HOLD
"""
from __future__ import annotations

import json
import re
import time

import openai

DECISION_KEYS = {
    "action": ("OPEN", "CLOSE", "MODIFY", "HOLD"),
    "bias": ("LONG", "SHORT", "FLAT"),
}


def _strip_to_json(text):
    if not text:
        return None
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


class LLMError(Exception):
    pass


class ValidationError(Exception):
    pass


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = cfg.get("model", "gpt-4o-mini")
        self.json_mode = cfg.get("json_mode", True)
        self.timeout = cfg.get("timeout_s", 15)
        self.retries = cfg.get("retries", 2)
        self.temperature = cfg.get("temperature", 0.2)
        self.max_tokens = cfg.get("max_tokens", 600)
        self.min_confidence = cfg.get("min_llm_confidence", 0.55)
        self.client = openai.OpenAI(
            base_url=cfg.get("base_url"),
            api_key=cfg.get("api_key", "none"),
            timeout=self.timeout,
            max_retries=0,
        )

    def _chat(self, system, user, temperature=None, max_tokens=None):
        temp = self.temperature if temperature is None else temperature
        mt = self.max_tokens if max_tokens is None else max_tokens
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temp,
            max_tokens=mt,
        )
        if self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content
        except openai.OpenAIError as e:
            # Beberapa proxy mengembalikan error pada response_format -> coba tanpa
            if self.json_mode and ("response_format" in str(e) or "Unsupported" in str(e)):
                self.json_mode = False
                kwargs.pop("response_format", None)
                resp = self.client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content
            raise LLMError(str(e)) from e

    def decide(self, system, user, temperature=None, max_tokens=None):
        last_err = None
        temp = self.temperature if temperature is None else temperature
        for attempt in range(self.retries + 1):
            try:
                raw = self._chat(system, user, temp, max_tokens)
                data = self.parse(raw)
                return self.validate(data)
            except ValidationError as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
            except (LLMError, openai.OpenAIError) as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise LLMError(f"LLM failed after retries: {last_err}")

    def parse(self, raw):
        snippet = _strip_to_json(raw)
        if not snippet:
            raise ValidationError("no JSON object in LLM output")
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as e:
            raise ValidationError(f"invalid JSON: {e}") from e

    def validate(self, data):
        if not isinstance(data, dict):
            raise ValidationError("decision must be an object")
        action = str(data.get("action", "HOLD")).upper()
        bias = str(data.get("bias", "FLAT")).upper()
        # sinonim natural trader: UP/DOWN/BULLISH/BEARISH -> LONG/SHORT
        if bias in ("UP", "BULLISH"):
            bias = "LONG"
        elif bias in ("DOWN", "BEARISH"):
            bias = "SHORT"
        # sinonim natural trader: BUY/SELL -> OPEN
        if action in ("BUY", "LONG", "SELL", "SHORT"):
            was_long = action in ("BUY", "LONG")
            action = "OPEN"
            if bias == "FLAT":
                bias = "LONG" if was_long else "SHORT"
        if action not in DECISION_KEYS["action"]:
            raise ValidationError(f"bad action: {action}")
        if bias not in DECISION_KEYS["bias"]:
            raise ValidationError(f"bad bias: {bias}")
        if action == "OPEN" and bias == "FLAT":
            raise ValidationError("OPEN requires LONG/SHORT bias")

        conf = float(data.get("confidence", 0.0))
        out = {
            "action": action,
            "bias": bias,
            "confidence": conf,
            "lot_fraction": _bound(data.get("lot_fraction"), 0.001, 0.05, 0.01),
            "entry": _float_or(data.get("entry"), None),
            "sl": _float_or(data.get("sl"), None),
            "tp": _float_or(data.get("tp"), None),
            "regime_label": str(data.get("regime_label", "")),
            "reasoning": str(data.get("reasoning", ""))[:300],
        }
        if action in ("OPEN",) and (out["entry"] is None or out["sl"] is None):
            # entry/SL opsional (EA hitung sendiri dari ATR) tapi lebih baik ada
            pass
        return out

    def llm_acceptable(self, decision, regime):
        """Gate: keputusan LLM hanya diterima bila confidence cukup dan
        konsisten dengan gate rule (mis. tidak OPEN saat regime CHOPPY)."""
        if decision["action"] == "OPEN":
            if decision["confidence"] < self.min_confidence:
                return False
            if regime.label in ("CHOPPY", "RANGING") and decision["bias"] != "FLAT":
                return False
        return True


def _float_or(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bound(v, lo, hi, default):
    try:
        f = float(v)
        return round(max(lo, min(hi, f)), 3)
    except (TypeError, ValueError):
        return default
