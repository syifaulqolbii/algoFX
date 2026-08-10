"""Backtest offline: replay data historis, jalankan regime layer + strategy
deterministik yang SAMA dengan fallback EA dan bridge. LLM tidak dipakai di
backtest (non-deterministik) — hasil ini = baseline untuk memvalidasi regime
layer, kemudian layer LLM diuji di live/demo.

Alur:
1. Data M5 (list dict time/o/h/l/c/v) dari CSV atau MetaTrader5 package.
2. Resample ke M15/H1/H4 per bar.
3. Untuk tiap bar: fitur pada window (sama seperti bridge) -> regime ->
   sinyal deterministik -> simulasikan fill (SL/TP dicek intra-bar).
"""
from __future__ import annotations

import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features import compute_series, features_at, rolling_rank, rolling_vol_ratio
from regime import classify, deterministic_signal

TF_DECISION = "M5"
TF_HIGHER = ["M15", "H1", "H4"]
WINDOW = 100          # sama dgn bars_per_tf di config (bridge)
BASE_BARS = {"M15": 3, "H1": 12, "H4": 48}   # kenaikan bar relatif M5


def resample(ohlcv, every):
    """Aggregate list bar M5 -> TF lebih tinggi. ohlcv: list dict."""
    out = []
    for i in range(0, len(ohlcv), every):
        chunk = ohlcv[i:i + every]
        if not chunk:
            continue
        out.append({
            "time": chunk[0]["time"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
        })
    return out


def run_backtest(m5_bars, cfg, initial_equity=10000.0, verbose=False):
    """Kembalikan dict statistik + trade list.

    Fitur dihitung SEKALI sebagai serial penuh (compute_series), lalu per bar
    dibaca via features_at — jauh lebih cepat untuk data besar.
    """
    n = len(m5_bars)
    if n < 300:
        raise ValueError("perlu minimal ~300 bar M5")

    rc = cfg.get("regime", {})
    ecfg = cfg.get("entry", {})
    window = cfg.get("bars_per_tf", 60)   # sama dgn bridge (bars_per_tf di config.yaml)
    swing_n = int(ecfg.get("swing_n", 20))
    lookback = int(ecfg.get("breakout_lookback", 3))
    ema_dist_pct = float(ecfg.get("pullback_ema_dist_pct", 0.15))
    # precompute serial seluruh TF + rolling rank/volume utk lookup O(1)
    m5_series = compute_series(_ohlcv_of(m5_bars), rc)
    m5_rank = rolling_rank(m5_series["atr_pct"], window)
    m5_volr = rolling_vol_ratio(m5_series["volume"], 20)
    htf = {}
    for tf, every in BASE_BARS.items():
        s = compute_series(_ohlcv_of(resample(m5_bars, every)), rc)
        htf[tf] = (s, rolling_rank(s["atr_pct"], window), rolling_vol_ratio(s["volume"], 20))

    acc = {"balance": initial_equity, "equity": initial_equity,
           "tick_size": cfg.get("tick_size", 1e-5), "tick_value": cfg.get("tick_value", 1.0)}
    pos = None          # dict side/entry/sl/tp/lots
    pending = None      # entry di bar berikutnya
    trades = []
    equity_curve = []
    regime_log = []
    max_eq = initial_equity
    max_dd = 0.0

    for i in range(60, n):
        bar = m5_bars[i]
        feats = {}
        f = _fast_feat(m5_series, m5_rank, m5_volr, i, window, swing_n, lookback, ema_dist_pct)
        if f:
            feats[TF_DECISION] = f
        for tf in TF_HIGHER:
            every = BASE_BARS[tf]
            # gunakan HANYA bar HTF yang sudah SELESAI (hindari lookahead).
            # Bar HTF ke-k selesai saat M5 index >= k*every + (every-1).
            idx = (i + 1) // every - 1
            if idx >= 0:
                s, rank, volr = htf[tf]
                g = _fast_feat(s, rank, volr, min(idx, len(s["close"]) - 1), window,
                               swing_n, lookback, ema_dist_pct)
                if g:
                    feats[tf] = g
        if not feats.get(TF_DECISION):
            continue

        higher = {tf: feats[tf] for tf in TF_HIGHER if tf in feats}
        regime = classify(feats[TF_DECISION], higher, rc)
        regime_log.append(regime.label)

        # isi pending dari bar sebelumnya
        if pending and pos is None:
            entry, sl, tp, lots, side = pending
            pos = {"side": side, "entry": entry, "sl": sl, "tp": tp, "lots": lots}
            pending = None
            # gap-through entry: open bar sudah melewati SL/TP -> close di open
            # (realistis; jangan di-SL/TP yang tidak tercapai saat gap)
            o = bar["open"]
            if (side == 1 and o <= sl) or (side == -1 and o >= sl):
                close_trade(pos, o, acc, trades, "gap_sl")
                pos = None
            elif (side == 1 and o >= tp) or (side == -1 and o <= tp):
                close_trade(pos, o, acc, trades, "gap_tp")
                pos = None

        # cek SL/TP intra-bar (konservatif: SL dihitung lebih dulu jika sama-sama kena)
        if pos:
            hi, lo = bar["high"], bar["low"]
            if pos["side"] == 1:
                hit_sl = lo <= pos["sl"]
                hit_tp = hi >= pos["tp"]
            else:
                hit_sl = hi >= pos["sl"]
                hit_tp = lo <= pos["tp"]
            exit_price = None
            if hit_sl and hit_tp:
                exit_price = pos["sl"]   # asumsi konservatif
                reason = "sl"
            elif hit_sl:
                exit_price = pos["sl"]; reason = "sl"
            elif hit_tp:
                exit_price = pos["tp"]; reason = "tp"
            if exit_price is not None:
                close_trade(pos, exit_price, acc, trades, reason)
                pos = None

        if pos is None:
            sig = deterministic_signal(feats, [TF_DECISION] + TF_HIGHER, regime,
                                       acc, cfg, cfg.get("spread", 0.0002),
                                       bar_time=bar["time"])
            if sig["action"] == "OPEN" and sig["lot"] > 0:
                # entry di open bar berikutnya utk hindari lookahead
                nxt = m5_bars[i + 1] if i + 1 < n else bar
                pending = (nxt["open"], sig["sl"], sig["tp"], sig["lot"], sig["side"])

        eq = acc["balance"] + (pos["lots"] * (bar["close"] - pos["entry"]) / acc["tick_size"]
                               * acc["tick_value"] * pos["side"] if pos else 0.0)
        equity_curve.append(eq)
        max_eq = max(max_eq, eq)
        max_dd = max(max_dd, max_eq - eq)

    # tutup sisa posisi di akhir
    if pos:
        close_trade(pos, m5_bars[-1]["close"], acc, trades, "end")

    stats = summarize(trades, equity_curve, regime_log, initial_equity)
    return stats, trades, equity_curve, regime_log


def _fast_feat(s, rank, volr, i, window, swing_n=20, lookback=3, ema_dist_pct=0.15):
    """Fitur bar ke-i dari serial + rolling rank pra-hitungan (parity features_at)."""
    if s is None or i < 60:
        return None
    ef, es = s["ema_fast"], s["ema_slow"]
    close, hi, lo = s["close"][i], s["high"][i], s["low"][i]
    bull = bool(ef[i] > es[i])
    cross = False
    for j in range(max(1, i - 5), i + 1):
        if (ef[j - 1] <= es[j - 1]) != (ef[j] <= es[j]):
            cross = True
            break
    sw_lo = max(0, i - swing_n + 1)
    lb = max(1, lookback)
    b_lo = max(0, i - lb)
    return {
        "close": float(close),
        "atr_pct": float(s["atr_pct"][i]),
        "atr_pct_percentile": float(rank[i]),
        "adx": float(s["adx"][i]),
        "er": float(s["er"][i]),
        "rsi": float(s["rsi"][i]),
        "ema_fast": float(ef[i]),
        "ema_slow": float(es[i]),
        "ema_fast_slope": float((es[i] - es[max(0, i - 3)]) / es[i] * 10000) if es[i] else 0.0,
        "ema_bull": bull,
        "ema_cross_recent": cross,
        "vol": float(s["volume"][i]),
        "volume_ratio": float(volr[i]),
        "range_pct": float((hi - lo) / close * 100),
        "last_return_pct": float((close / s["close"][i - 1] - 1) * 100) if i > 0 else 0.0,
        "prev_return_pct": float((s["close"][i - 1] / s["close"][i - 2] - 1) * 100) if i > 1 else 0.0,
        # --- entry context (parity features_at) ---
        "swing_high": float(s["high"][sw_lo:i + 1].max()),
        "swing_low": float(s["low"][sw_lo:i + 1].min()),
        "close_vs_ema_slow": float((close / es[i] - 1) * 100) if es[i] else 0.0,
        "near_ema_fast": bool(abs(close - ef[i]) / ef[i] * 100 <= ema_dist_pct),
        "breakout_high": bool(close > float(s["high"][b_lo:i].max()) if b_lo < i else False),
        "breakout_low": bool(close < float(s["low"][b_lo:i].min()) if b_lo < i else False),
        "last_bar_down": bool(close < s["close"][i - 1]) if i > 0 else False,
        "last_bar_up": bool(close > s["close"][i - 1]) if i > 0 else False,
    }


def _ohlcv_of(bars):
    import numpy as np
    return {
        "open": np.asarray([b["open"] for b in bars], dtype=float),
        "high": np.asarray([b["high"] for b in bars], dtype=float),
        "low": np.asarray([b["low"] for b in bars], dtype=float),
        "close": np.asarray([b["close"] for b in bars], dtype=float),
        "volume": np.asarray([b["volume"] for b in bars], dtype=float),
    }


def close_trade(pos, price, acc, trades, reason):
    tick_value = max(acc.get("tick_value", 1.0), 1e-9)
    tick_size = max(acc.get("tick_size", 1e-5), 1e-9)
    pnl = pos["lots"] * (price - pos["entry"]) / tick_size * tick_value * pos["side"]
    acc["balance"] += pnl
    trades.append({
        "side": "LONG" if pos["side"] == 1 else "SHORT",
        "entry": pos["entry"], "exit": price, "lots": pos["lots"],
        "pnl": round(pnl, 2), "reason": reason,
    })


def summarize(trades, equity_curve, regime_log, initial_equity):
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    eq = np.asarray(equity_curve)
    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-9)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
        "net_profit": round(sum(t["pnl"] for t in trades), 2),
        "max_drawdown": round(float(np.max(np.maximum.accumulate(eq) - eq)), 2),
        "final_equity": round(float(eq[-1]), 2) if len(eq) else initial_equity,
        "sharpe": round(float(rets.mean() / rets.std() * np.sqrt(252 * 288)) if len(rets) > 2 and rets.std() > 0 else 0.0, 2),
        "regime_dist": {r: round(regime_log.count(r) / len(regime_log), 3) if regime_log else 0.0
                        for r in ("TREND_UP", "TREND_DOWN", "RANGING", "CHOPPY", "MIXED")},
    }
