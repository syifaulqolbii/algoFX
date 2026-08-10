"""Uji pipeline features -> regime -> decision dengan data sintetis."""
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features import compute_features, multi_tf_snapshot
from regime import classify, deterministic_signal
from config import load_config


def gen_bars(n=200, start=1.1000, drift=0.0, vol=0.0004, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    c = start * np.exp(np.cumsum(rets))
    h = c * (1 + np.abs(rng.normal(0, vol, n)))
    l = c * (1 - np.abs(rng.normal(0, vol, n)))
    o = np.roll(c, 1); o[0] = c[0]
    v = rng.uniform(80, 120, n)
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def main():
    cfg = load_config()
    scenarios = {
        "TREND_UP": gen_bars(drift=0.0012, vol=0.0003, seed=3),
        "RANGING":  gen_bars(drift=0.0, vol=0.00015, seed=4),
        "CHOPPY":   gen_bars(drift=0.0, vol=0.0012, seed=5),
    }
    tf_order = ["M5", "M15", "H1", "H4"]
    higher_up = {tf: compute_features(gen_bars(drift=0.0008, seed=10 + i)) for i, tf in enumerate(tf_order[1:])}

    for name, bars in scenarios.items():
        f = compute_features(bars, rc={**cfg["regime"], **cfg.get("entry", {})})
        print(f"--- {name} ---")
        print("  features:", {k: round(v, 3) if isinstance(v, float) else v
                              for k, v in f.items() if k not in ("close", "swing_high", "swing_low")})
        r = classify(f, higher_up, cfg["regime"])
        print("  regime:", r.to_dict())
        for etype in ("market", "pullback", "breakout"):
            c = dict(cfg)
            c["entry"] = {**cfg.get("entry", {}), "type": etype}
            sig = deterministic_signal({tf_order[0]: f}, tf_order, r,
                                       {"equity": 10000, "tick_size": 1e-5, "tick_value": 1.0},
                                       c, 0.0002)
            print(f"  signal[{etype:>9s}]:", sig["action"], sig["bias"], "lot=", sig["lot"])


if __name__ == "__main__":
    main()
