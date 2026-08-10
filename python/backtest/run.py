"""Generator data M5 sintetis + CLI backtest.

Pemakaian:
  python -m backtest.run --bars 8000 --seed 7
  python -m backtest.run --csv path/file.csv   (kolom: time,open,high,low,close,volume)
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.engine import run_backtest
from config import load_config


def regime_switching(n=8000, seed=7, start=1.10):
    """Sintesis harga dengan regime switching (trend/range/chop)."""
    rng = np.random.default_rng(seed)
    vol_base = 0.0004
    out = []
    price = start
    t = 1700000000
    regime_drift = 0.0
    for i in range(n):
        if i % 400 == 0:
            r = rng.random()
            if r < 0.4:
                regime_drift = rng.choice([0.0012, -0.0012])
                vol = vol_base * 0.8
            elif r < 0.7:
                regime_drift = 0.0
                vol = vol_base * 0.4
            else:
                regime_drift = 0.0
                vol = vol_base * 2.2
        ret = rng.normal(regime_drift, vol)
        o = price
        c = o * np.exp(ret)
        hi = max(o, c) * (1 + abs(rng.normal(0, vol * 0.6)))
        lo = min(o, c) * (1 - abs(rng.normal(0, vol * 0.6)))
        v = float(rng.uniform(80, 120) * (1 + min(vol / vol_base, 3) * 0.5))
        out.append({"time": t, "open": o, "high": hi, "low": lo, "close": c, "volume": v})
        price = c
        t += 300  # 5 menit
    return out


def load_csv(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: float(v) if k != "time" else int(float(v))
                         for k, v in r.items()})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--symbol", default="EURUSD")
    args = ap.parse_args()

    cfg = load_config()
    if args.csv:
        bars = load_csv(args.csv)
    else:
        bars = regime_switching(args.bars, args.seed)

    stats, trades, curve, regimes = run_backtest(bars, cfg)
    print(f"=== Backtest {args.symbol} | bars={len(bars)} ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("  sample trades:", trades[:3])


if __name__ == "__main__":
    main()
