"""Mock LLM untuk testing / dry-run tanpa biaya API.

Output mengikuti schema yang sama dengan LLM asli, dihasilkan dari logika
deterministik regime layer. Cocok dipakai untuk uji alur bridge end-to-end.
"""
from __future__ import annotations

import json
import re


def _extract_json(text):
    if not isinstance(text, str):
        return text
    start = text.find("{")
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


class MockLLM:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.json_mode = True
        self.min_confidence = (cfg or {}).get("min_llm_confidence", 0.55)

    def llm_acceptable(self, decision, regime):
        """Gate identik dengan LLMClient: tolak OPEN saat regime chop/range atau conf rendah."""
        if decision["action"] == "OPEN":
            if decision["confidence"] < self.min_confidence:
                return False
            if regime.label in ("CHOPPY", "RANGING") and decision["bias"] != "FLAT":
                return False
        return True

    def decide(self, system, user, temperature=None, max_tokens=None):
        # user = teks + snapshot JSON; ambil blok JSON saja
        payload = _extract_json(user) or {}
        regime = payload.get("regime", {})
        label = regime.get("label", "MIXED")
        conf = regime.get("confidence", 0.0)
        primary = payload.get("features", {}).get("M5", {})

        decision = {
            "action": "HOLD",
            "bias": "FLAT",
            "confidence": round(conf, 2),
            "lot_fraction": 0.01,
            "entry": None,
            "sl": None,
            "tp": None,
            "regime_label": label,
            "reasoning": f"mock: regime {label} conf={conf:.2f}",
        }
        if label in ("TREND_UP", "TREND_DOWN") and conf >= self.min_confidence:
            long = label == "TREND_UP"
            close = primary.get("close", 1.0)
            atr = primary.get("atr%", 0.1) / 100 * close
            decision.update({
                "action": "OPEN",
                "bias": "LONG" if long else "SHORT",
                "confidence": round(conf, 2),
                "entry": round(close, 5),
                "sl": round(close - 1.5 * atr if long else close + 1.5 * atr, 5),
                "tp": round(close + 2.25 * atr if long else close - 2.25 * atr, 5),
                "reasoning": f"mock trend-follow {label}",
            })
        return decision
