"""Parameter sweep baseline deterministik pada data M5.

Pemakaian:
  python -m backtest.sweep --csv data/EURUSD_M5.csv --bars 60000
"""
import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.engine import run_backtest
from backtest.run import load_csv
from config import load_config

GRID = {
    "trend_adx_min": [22, 28, 34],
    "trend_er_min": [0.35, 0.50],
    "min_confidence": [0.60, 0.75],
    "rr_target": [1.5, 3.0],
    "entry_type": ["market", "pullback", "breakout"],
    "require_htf_alignment": [False, True],
}


def select_best_params(bars, base_cfg, grid, min_trades=30):
    """Jalankan grid pada `bars`, kembalikan cfg terbaik (by net_profit)."""
    best_cfg, best_net = None, float("-inf")
    for adx, er, conf, rr, etype, htf in itertools.product(
            grid["trend_adx_min"], grid["trend_er_min"],
            grid["min_confidence"], grid["rr_target"],
            grid["entry_type"], grid["require_htf_alignment"]):
        c = dict(base_cfg)
        c["trading"] = {**base_cfg["trading"], "min_confidence": conf, "rr_target": rr}
        c["regime"] = {**base_cfg["regime"], "trend_adx_min": adx, "trend_er_min": er}
        c["entry"] = {**base_cfg.get("entry", {}), "type": etype,
                      "require_htf_alignment": htf}
        stats, _, _, _ = run_backtest(bars, c)
        if stats["trades"] < min_trades:
            continue
        if stats["net_profit"] > best_net:
            best_net = stats["net_profit"]
            best_cfg = c
    return best_cfg, best_net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--bars", type=int, default=60000)
    ap.add_argument("--min-trades", type=int, default=30)
    args = ap.parse_args()

    cfg = load_config()
    bars = load_csv(args.csv)[-args.bars:]
    print(f"Data: {len(bars)} bar | {args.csv}")

    rows = []
    for adx, er, conf, rr, etype, htf in itertools.product(
            GRID["trend_adx_min"], GRID["trend_er_min"],
            GRID["min_confidence"], GRID["rr_target"],
            GRID["entry_type"], GRID["require_htf_alignment"]):
        c = dict(cfg)
        c["trading"] = {**cfg["trading"], "min_confidence": conf, "rr_target": rr}
        c["regime"] = {**cfg["regime"], "trend_adx_min": adx, "trend_er_min": er}
        c["entry"] = {**cfg.get("entry", {}), "type": etype,
                      "require_htf_alignment": htf}
        stats, _, _, _ = run_backtest(bars, c)
        rows.append((adx, er, conf, rr, etype, htf, stats))
        print(f"adx={adx:>3} er={er:.2f} conf={conf:.2f} rr={rr:>3} type={etype:<9} htf={int(htf)} | "
              f"trades={stats['trades']:>4} win={stats['win_rate']:.3f} "
              f"PF={stats['profit_factor']:>5.2f} net={stats['net_profit']:>9.0f} "
              f"DD={stats['max_drawdown']:>7.0f}")

    best = max(rows, key=lambda r: r[6]["net_profit"])
    print("\n=== TERBAIK ===")
    adx, er, conf, rr, etype, htf, s = best
    print(f"adx={adx} er={er} conf={conf} rr={rr} type={etype} htf={htf} | "
          f"trades={s['trades']} win={s['win_rate']} PF={s['profit_factor']} "
          f"net={s['net_profit']} DD={s['max_drawdown']}")
    print("\n=== Cek profit -> harus trade >= 30 & PF >= 1.1 untuk dianggap viable ===")


if __name__ == "__main__":
    main()
