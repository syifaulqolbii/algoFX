"""Klasifikasi regime market deterministik.

Logika ini WAJIB identik dengan include/Deterministic.mqh di MQL5 agar
backtest MT5 (mode tester) mencerminkan baseline yang sama dengan
offline backtest Python. Seluruh threshold berasal dari config.yaml.
"""
from __future__ import annotations

import numpy as np

REGIME_LABELS = ("TREND_UP", "TREND_DOWN", "RANGING", "CHOPPY", "MIXED")


class Regime:
    def __init__(self, label, confidence, scores, detail=None):
        self.label = label
        self.confidence = confidence
        self.scores = scores  # dict label -> float (softmax-like)
        self.detail = detail or {}

    def to_dict(self):
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "detail": self.detail,
        }


def _softmax(scores):
    arr = np.array(list(scores.values()), dtype=float)
    e = np.exp(arr - arr.max())
    p = e / e.sum()
    return dict(zip(scores.keys(), p))


def classify(primary, higher=None, rc=None):
    """Klasifikasi regime dari fitur TF utama + konteks TF lebih tinggi.

    primary: dict fitur dari features.compute_features (TF decision)
    higher : {tf_name: features} untuk konteks trend TF besar (H1/H4)
    rc     : regime config dari config.yaml
    """
    rc = rc or {}
    trend_er_min = rc.get("trend_er_min", 0.35)
    trend_adx_min = rc.get("trend_adx_min", 22)
    range_er_max = rc.get("range_er_max", 0.18)
    range_adx_max = rc.get("range_adx_max", 20)
    hv_pct = rc.get("highvol_percentile", 0.80)

    if primary is None:
        return Regime("MIXED", 0.0, {l: 0.0 for l in REGIME_LABELS})

    er, adx = primary["er"], primary["adx"]
    ema_bull = primary["ema_bull"]
    hv = primary["atr_pct_percentile"]

    # Konteks trend dari TF lebih tinggi (H1/H4): mayoritas ema_bull
    ctx_bull = None
    if higher:
        bulls = [f["ema_bull"] for f in higher.values() if f]
        if bulls:
            ctx_bull = sum(bulls) >= (len(bulls) / 2)

    raw = {l: 0.0 for l in REGIME_LABELS}

    if er > trend_er_min and adx > trend_adx_min:
        if ema_bull:
            raw["TREND_UP"] = 1.0 + (er - trend_er_min) * 2 + (adx - trend_adx_min) / 30
        else:
            raw["TREND_DOWN"] = 1.0 + (er - trend_er_min) * 2 + (adx - trend_adx_min) / 30
        # konteks TF besar searah menambah skor, berlawanan mengurangi
        if ctx_bull is not None:
            if ema_bull == ctx_bull:
                raw["TREND_UP" if ema_bull else "TREND_DOWN"] += 0.5
            else:
                raw["TREND_UP" if ema_bull else "TREND_DOWN"] -= 0.5
    elif er < range_er_max and adx < range_adx_max and hv < hv_pct:
        raw["RANGING"] = 1.0 + (range_er_max - er) + (range_adx_max - adx) / 30
    elif hv >= hv_pct and er < trend_er_min:
        raw["CHOPPY"] = 1.0 + (hv - hv_pct) * 3
    else:
        raw["MIXED"] = 1.0

    scores = _softmax(raw)
    label = max(scores, key=scores.get)
    conf = scores[label]
    detail = {
        "er": round(er, 3),
        "adx": round(adx, 1),
        "ema_bull": ema_bull,
        "hv_percentile": round(hv, 2),
        "ctx_bull": ctx_bull,
    }
    return Regime(label, conf, scores, detail)


def _distance_in_points(price, sl, tick_value, tick_size):
    """SL distance dalam poin harga (untuk sizing)."""
    dist = abs(price - sl)
    return max(dist / tick_size, 1e-9), dist


def _hour_ok(bar_time, s_cfg, server_offset=0):
    """Session filter. bar_time: UNIX detik. Kembalikan True bila boleh entry."""
    if bar_time is None or not s_cfg.get("enabled", False):
        return True
    h = (int(bar_time) % 86400) // 3600
    h = (h + int(server_offset)) % 24
    quiet = s_cfg.get("quiet_hours")
    if quiet:
        qs, qe = quiet
        if qs <= qe:
            if qs <= h < qe:
                return False
        else:  # melewati tengah malam
            if h >= qs or h < qe:
                return False
    eh = s_cfg.get("entry_hours")
    if eh:
        es_, ee = eh
        if es_ <= ee:
            if not (es_ <= h < ee):
                return False
        else:
            if not (h >= es_ or h < ee):
                return False
    return True


def _htf_aligned(features_by_tf, tf_order, want_long, ecfg):
    """Gate: minimal `min_htf_agree` TF besar (M15/H1/H4) searah arah entry."""
    higher = [features_by_tf[tf] for tf in tf_order[1:] if tf in features_by_tf]
    if not higher:
        return True
    req = int(ecfg.get("min_htf_agree", 2))
    req = max(1, min(req, len(higher)))
    agree = sum(1 for f in higher if f["ema_bull"] == want_long)
    return agree >= req


def _entry_ok(f, ecfg, want_long):
    """Kualifikasi tipe entry: market | pullback | breakout."""
    etype = ecfg.get("type", "market")
    if etype == "market":
        return True
    if etype == "pullback":
        cvs = f.get("close_vs_ema_slow", 0.0)
        if want_long:
            return bool(f.get("last_bar_down") and f.get("near_ema_fast") and cvs > -0.5)
        return bool(f.get("last_bar_up") and f.get("near_ema_fast") and cvs < 0.5)
    if etype == "breakout":
        return bool(f.get("breakout_high")) if want_long else bool(f.get("breakout_low"))
    return True


def compute_lot(equity, sl_dist, tick_size, tick_value, risk_cfg,
                atr_pct=None, confidence=None):
    """Ukuran posisi berbasis risk (sizing terpusat utk bridge & backtest)."""
    risk_pct = risk_cfg.get("risk_per_trade_pct", 0.5) / 100.0
    mult = 1.0
    vt = risk_cfg.get("vol_target") or {}
    if vt.get("enabled") and atr_pct:
        t = vt.get("target_atr_pct", 0.05)
        m = t / max(atr_pct, 1e-9)
        mult *= max(vt.get("min_mult", 0.5), min(vt.get("max_mult", 2.0), m))
    cs = risk_cfg.get("confidence_scaling") or {}
    if cs.get("enabled") and confidence is not None:
        m = confidence / cs.get("base_confidence", 0.70)
        mult *= max(cs.get("min_mult", 0.5), min(cs.get("max_mult", 1.5), m))
    risk_amt = equity * risk_pct * mult
    lot = risk_amt / max(sl_dist * tick_value / tick_size, 1e-9)
    step = risk_cfg.get("lot_step", 0.01)
    lot = round(lot / step) * step
    lot = max(risk_cfg.get("min_lot", 0.01), min(lot, risk_cfg.get("max_lot", 1.0)))
    return round(lot, 4)


def deterministic_signal(features_by_tf, tf_order, regime, account, rc, spread,
                         bar_time=None):
    """Signal trading deterministik (fallback + backtest baseline).

    returns dict:
      {action, bias, side, entry, sl, tp, lot, reasoning}
    action: OPEN | HOLD | CLOSE
    """
    risk_cfg = rc.get("risk", {})
    tcfg = rc.get("trading", {})
    rcfg = rc.get("regime", {})
    ecfg = rc.get("entry", {})
    min_conf = tcfg.get("min_confidence", 0.60)
    rr = tcfg.get("rr_target", 1.5)
    atr_sl_mult = tcfg.get("atr_sl_mult", 1.5)

    primary = features_by_tf[tf_order[0]]
    tick_size = account.get("tick_size", 1e-5)
    tick_value = account.get("tick_value", 1.0)
    equity = account.get("equity", account.get("balance", 10000.0))
    atr = primary["atr_pct"] / 100.0 * primary["close"]

    base = {
        "action": "HOLD",
        "bias": "FLAT",
        "side": 0,
        "entry": None, "sl": None, "tp": None, "lot": 0.0,
        "reasoning": "no signal",
    }

    if regime.label not in ("TREND_UP", "TREND_DOWN"):
        if regime.label in ("RANGING", "CHOPPY"):
            base["reasoning"] = f"{regime.label} => wait for trend setup"
        return base

    want_long = regime.label == "TREND_UP"
    if regime.confidence < min_conf:
        base["reasoning"] = f"{regime.label} conf={regime.confidence:.2f} < {min_conf:.2f}"
        return base
    if not _hour_ok(bar_time, rc.get("session", {})):
        base["reasoning"] = "session filter => skip"
        return base
    if ecfg.get("require_htf_alignment", False) and not _htf_aligned(features_by_tf, tf_order, want_long, ecfg):
        base["reasoning"] = "HTF tidak searah => skip"
        return base
    if not _entry_ok(primary, ecfg, want_long):
        base["reasoning"] = f"entry type '{ecfg.get('type','market')}' tidak terpenuhi"
        return base

    entry = primary["close"]
    sl_dist = max(atr_sl_mult * atr, spread * 2)
    if want_long:
        sl = entry - sl_dist
        tp = entry + sl_dist * rr
        side = 1
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * rr
        side = -1
    lot = compute_lot(equity, sl_dist, tick_size, tick_value, risk_cfg,
                      atr_pct=primary["atr_pct"], confidence=regime.confidence)
    return {
        "action": "OPEN", "bias": "LONG" if want_long else "SHORT",
        "side": side, "entry": round(entry, 5), "sl": round(sl, 5),
        "tp": round(tp, 5), "lot": lot,
        "reasoning": f"{regime.label} conf={regime.confidence:.2f} adx={primary['adx']:.1f} er={primary['er']:.2f} type={ecfg.get('type','market')}",
    }
