"""Perhitungan fitur teknikal multi-timeframe.

Semua fungsi murni (pure function) di numpy agar identik dengan
implementasi MQL5 (include/Features.mqh) dan bisa dipakai ulang di backtest.
"""
from __future__ import annotations

import numpy as np

DEFAULT_RC = {
    "atr_period": 14,
    "adx_period": 14,
    "vol_period": 20,
    "ema_fast": 9,
    "ema_slow": 21,
    "rsi_period": 14,
    "er_period": 20,
}


def ema(values, period):
    a = 2.0 / (period + 1)
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * a + out[i - 1] * (1 - a)
    return out


def _ewm(values, period):
    """EMA-style smoothing alfa=1/period (identik Wilder smoothing)."""
    a = 1.0 / period
    out = np.empty(len(values))
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * a + out[i - 1] * (1 - a)
    return out


def rsi(close, period):
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    # Wilder smoothing
    ag = np.zeros(len(close))
    al = np.zeros(len(close))
    ag[period] = gain[:period].mean()
    al[period] = loss[:period].mean()
    for i in range(period + 1, len(close)):
        ag[i] = (ag[i - 1] * (period - 1) + gain[i - 1]) / period
        al[i] = (al[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.where(al == 0, 100.0, ag / np.maximum(al, 1e-12))
    out = np.full(len(close), 50.0)
    out[period:] = 100.0 - 100.0 / (1.0 + rs[period:])
    return out


def true_range(high, low, close):
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def atr(high, low, close, period):
    tr = true_range(high, low, close)
    a = 1.0 / period
    out = np.zeros(len(tr))
    out[period] = tr[:period + 1].mean()
    for i in range(period + 1, len(tr)):
        out[i] = out[i - 1] * (1 - a) + tr[i] * a
    out[:period] = out[period]
    return out


def adx(high, low, close, period):
    n = len(close)
    if n < period + 2:
        return np.zeros(n)
    up = np.diff(high, prepend=high[0])
    dn = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(high, low, close)
    atr_w = np.maximum(_ewm(tr, period), 1e-12)
    pdi = 100.0 * _ewm(plus_dm, period) / atr_w
    mdi = 100.0 * _ewm(minus_dm, period) / atr_w
    dx = 100.0 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    return _ewm(dx, period)


def efficiency_ratio(close, period):
    n = len(close)
    out = np.zeros(n)
    for i in range(period, n):
        direction = abs(close[i] - close[i - period])
        vol = np.sum(np.abs(np.diff(close[i - period:i + 1])))
        out[i] = direction / vol if vol > 0 else 0.0
    out[:period] = out[period]
    return out


def realized_vol(close, period, bars_per_year):
    logret = np.log(np.maximum(close, 1e-12))
    lr = np.diff(logret)
    out = np.zeros(len(close))
    for i in range(period, len(close)):
        s = np.std(lr[i - period:i], ddof=1)
        out[i] = s * np.sqrt(bars_per_year)
    out[:period] = out[period]
    return out


def atr_pct_series(close, atr_series):
    return atr_series / np.maximum(close, 1e-12) * 100.0


def percentile_rank(series, value):
    """Persentil nilai `value` terhadap history series (0..1)."""
    valid = series[series > 0]
    if len(valid) == 0:
        return 0.5
    return float((valid < value).mean())


def compute_features(ohlcv, rc=None, atr_series=None):
    """Hitung fitur lengkap untuk satu array OHLCV (numpy).

    ohlcv: dict open/high/low/close/volume → np.array (terbaru di index terakhir)
    returns: dict fitur scalar di bar terakhir.
    """
    rc = {**DEFAULT_RC, **(rc or {})}
    o, h, l, c, v = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
    n = len(c)
    if n < 60:
        return None

    a = atr(h, l, c, rc["atr_period"])
    dx = adx(h, l, c, rc["adx_period"])
    er = efficiency_ratio(c, rc["er_period"])
    r = rsi(c, rc["rsi_period"])
    ef = ema(c, rc["ema_fast"])
    es = ema(c, rc["ema_slow"])
    ap = atr_pct_series(c, a)

    last = n - 1
    slope = (es[last] - es[last - 3]) / es[last] * 10000 if es[last] else 0.0
    swing_n = int(rc.get("swing_n", 20))
    lb = max(1, int(rc.get("breakout_lookback", 3)))
    s_lo = max(0, last - swing_n + 1)
    b_lo = max(0, last - lb)
    swing_high = float(h[s_lo:last + 1].max())
    swing_low = float(l[s_lo:last + 1].min())
    return {
        "close": float(c[last]),
        "atr_pct": float(ap[last]),
        "atr_pct_percentile": percentile_rank(ap, ap[last]),
        "adx": float(dx[last]),
        "er": float(er[last]),
        "rsi": float(r[last]),
        "ema_fast": float(ef[last]),
        "ema_slow": float(es[last]),
        "ema_fast_slope": slope,
        "ema_bull": bool(ef[last] > es[last]),
        "ema_cross_recent": _recent_cross(ef, es, last),
        "vol": float(v[last]),
        "volume_ratio": _volume_ratio(v, last),
        "range_pct": float((h[last] - l[last]) / c[last] * 100),
        "last_return_pct": float((c[last] / c[last - 1] - 1) * 100) if n > 1 else 0.0,
        "prev_return_pct": float((c[last - 1] / c[last - 2] - 1) * 100) if n > 2 else 0.0,
        # --- entry context ---
        "swing_high": swing_high,
        "swing_low": swing_low,
        "swing_mid": (swing_high + swing_low) / 2,
        "close_vs_ema_slow": float((c[last] / es[last] - 1) * 100) if es[last] else 0.0,
        "near_ema_fast": bool(abs(c[last] - ef[last]) / ef[last] * 100 <= float(rc.get("pullback_ema_dist_pct", 0.15))),
        "breakout_high": bool(c[last] > float(h[b_lo:last].max()) if b_lo < last else False),
        "breakout_low": bool(c[last] < float(l[b_lo:last].min()) if b_lo < last else False),
        "last_bar_down": bool(c[last] < c[last - 1]) if n > 1 else False,
        "last_bar_up": bool(c[last] > c[last - 1]) if n > 1 else False,
    }


def _recent_cross(ef, es, last, lookback=5):
    for i in range(max(1, last - lookback), last + 1):
        if (ef[i - 1] <= es[i - 1]) != (ef[i] <= es[i]):
            return True
    return False


def _volume_ratio(v, last, window=20):
    if last < window:
        return 1.0
    avg = v[last - window:last].mean()
    return float(v[last] / avg) if avg > 0 else 1.0


def compute_series(ohlcv, rc=None):
    """Hitung seluruh serial indikator sekali (O(n)) untuk backtest cepat.

    Kembalikan dict numpy array sepanjang n; nilai di bar terakhir identik
    dengan compute_features.
    """
    rc = {**DEFAULT_RC, **(rc or {})}
    o, h, l, c, v = ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"]
    n = len(c)
    if n < 60:
        return None
    a = atr(h, l, c, rc["atr_period"])
    ap = atr_pct_series(c, a)
    return {
        "close": c, "high": h, "low": l, "atr_pct": ap, "atr": a,
        "adx": adx(h, l, c, rc["adx_period"]),
        "er": efficiency_ratio(c, rc["er_period"]),
        "rsi": rsi(c, rc["rsi_period"]),
        "ema_fast": ema(c, rc["ema_fast"]),
        "ema_slow": ema(c, rc["ema_slow"]),
        "volume": v,
    }


def features_at(series, i, window=100, vol_window=20, swing_n=20, lookback=3,
                ema_dist_pct=0.15):
    """Fitur bar ke-i dari serial pra-hitungan (parity dengan compute_features)."""
    s = series
    if s is None or i < 60:
        return None
    lo = max(0, i - window + 1)
    ap = s["atr_pct"]
    ef, es = s["ema_fast"], s["ema_slow"]
    hi, lo_price = s["high"][i], s["low"][i]
    close = s["close"][i]
    sw_lo = max(0, i - swing_n + 1)
    lb = max(1, lookback)
    b_lo = max(0, i - lb)
    swing_high = float(s["high"][sw_lo:i + 1].max())
    swing_low = float(s["low"][sw_lo:i + 1].min())
    return {
        "close": float(close),
        "atr_pct": float(ap[i]),
        "atr_pct_percentile": percentile_rank(ap[lo:i + 1], ap[i]),
        "adx": float(s["adx"][i]),
        "er": float(s["er"][i]),
        "rsi": float(s["rsi"][i]),
        "ema_fast": float(ef[i]),
        "ema_slow": float(es[i]),
        "ema_fast_slope": float((es[i] - es[max(0, i - 3)]) / es[i] * 10000) if es[i] else 0.0,
        "ema_bull": bool(ef[i] > es[i]),
        "ema_cross_recent": _recent_cross(ef, es, i),
        "vol": float(s["volume"][i]),
        "volume_ratio": _volume_ratio(s["volume"], i, vol_window),
        "range_pct": float((hi - lo_price) / close * 100),
        "last_return_pct": float((close / s["close"][i - 1] - 1) * 100) if i > 0 else 0.0,
        "prev_return_pct": float((s["close"][i - 1] / s["close"][i - 2] - 1) * 100) if i > 1 else 0.0,
        # --- entry context (parity compute_features) ---
        "swing_high": swing_high,
        "swing_low": swing_low,
        "swing_mid": (swing_high + swing_low) / 2,
        "close_vs_ema_slow": float((close / es[i] - 1) * 100) if es[i] else 0.0,
        "near_ema_fast": bool(abs(close - ef[i]) / ef[i] * 100 <= ema_dist_pct),
        "breakout_high": bool(close > float(s["high"][b_lo:i].max()) if b_lo < i else False),
        "breakout_low": bool(close < float(s["low"][b_lo:i].min()) if b_lo < i else False),
        "last_bar_down": bool(close < s["close"][i - 1]) if i > 0 else False,
        "last_bar_up": bool(close > s["close"][i - 1]) if i > 0 else False,
    }


def rolling_rank(series, window):
    """Rolling percentile-rank (0..1) dari tiap nilai terhadap `window` bar
    terakhir. O(n*window) sekali jalan; hasilnya dipakai lookup O(1) di backtest."""
    n = len(series)
    out = np.full(n, 0.5)
    for i in range(n):
        lo = max(0, i - window + 1)
        win = series[lo:i + 1]
        valid = win[win > 0]
        if len(valid) == 0:
            out[i] = 0.5
        else:
            out[i] = (valid < series[i]).mean()
    return out


def rolling_vol_ratio(volume, window):
    """Rolling volume ratio terhadap rata-rata `window` bar sebelumnya."""
    n = len(volume)
    out = np.ones(n)
    for i in range(window, n):
        avg = volume[i - window:i].mean()
        out[i] = float(volume[i] / avg) if avg > 0 else 1.0
    for i in range(1, window):
        avg = volume[:i].mean()
        out[i] = float(volume[i] / avg) if avg > 0 else 1.0
    return out


def multi_tf_snapshot(features_by_tf, tf_order):
    """Gabungkan fitur per TF jadi dict JSON ringkas untuk LLM."""
    out = {}
    for tf in tf_order:
        f = features_by_tf.get(tf)
        if f:
            out[tf] = {
                "close": round(f["close"], 5),
                "atr%": round(f["atr_pct"], 3),
                "adx": round(f["adx"], 1),
                "er": round(f["er"], 2),
                "rsi": round(f["rsi"], 1),
                "emaBull": f["ema_bull"],
                "emaX": f["ema_cross_recent"],
                "volR": round(f["volume_ratio"], 2),
                "rng%": round(f["range_pct"], 3),
                "ret%": round(f["last_return_pct"], 3),
                "atrPct": round(f["atr_pct_percentile"], 2),
            }
    return out
