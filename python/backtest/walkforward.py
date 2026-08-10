"""Walk-forward validation untuk parameter strategi deterministik.

Dua mode:
  1) Rolling per-simbol: train pada bagian awal data, pilih best param (grid),
     tes pada bagian akhir (out-of-sample), agregat lintas simbol.
  2) Leave-one-out tuning: tune di simbol utama (default EURUSD), lalu evaluasi
     config terpilih di SEMUA simbol (generalization antar simbol & periode).

Pemakaian:
  python -m backtest.walkforward --mode roll
  python -m backtest.walkforward --mode oos --tune EURUSD
"""
import argparse
import glob
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.engine import run_backtest
from backtest.run import load_csv
from backtest.sweep import select_best_params
from config import load_config

GRID = {
    "trend_adx_min": [34],
    "trend_er_min": [0.50],
    "min_confidence": [0.60],
    "rr_target": [1.5, 3.0],
    "entry_type": ["market", "pullback", "breakout"],
    "require_htf_alignment": [False],
}

# tick_size/tick_value per simbol (approx utk PnL)
TICKS = {
    "EURUSD": (1e-5, 1.0), "GBPUSD": (1e-5, 1.0), "AUDUSD": (1e-5, 1.0),
    "USDCAD": (1e-5, 1.0), "USDCHF": (1e-5, 1.0), "USDJPY": (0.01, 6.5),
    "XAUUSD": (0.01, 1.0),
}


def data_files():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return sorted(glob.glob(os.path.join(d, "*_M5.csv")))


def sym_of(path):
    return os.path.basename(path).split("_")[0]


def apply_ticks(cfg, sym):
    ts, tv = TICKS.get(sym, (1e-5, 1.0))
    c = dict(cfg)
    c["tick_size"], c["tick_value"] = ts, tv
    return c


def mode_roll(train_frac=0.6, max_bars=80000):
    print("=== ROLLING WALK-FORWARD (train=%.0f%%, test=%.0f%%) ===" % (train_frac * 100, (1 - train_frac) * 100))
    print("%-8s %-24s %-24s" % ("symbol", "train(PF/net/type/rr)", "OOS(PF/net/DD/type/rr)"))
    oos_pf, oos_net = [], []
    for path in data_files():
        sym = sym_of(path)
        bars = load_csv(path)[-max_bars:]
        split = int(len(bars) * train_frac)
        base = apply_ticks(load_config(), sym)
        best_cfg, _ = select_best_params(bars[:split], base, GRID)
        s_tr, *_ = run_backtest(bars[:split], best_cfg)
        s_oos, *_ = run_backtest(bars[split:], best_cfg)
        oos_pf.append(s_oos["profit_factor"])
        oos_net.append(s_oos["net_profit"])
        print("%-8s tr(PF=%.2f net=%7.0f t=%s rr=%s) oos(PF=%.2f net=%7.0f DD=%6.0f t=%s rr=%s)"
              % (sym, s_tr["profit_factor"], s_tr["net_profit"],
                 best_cfg["entry"]["type"], best_cfg["trading"]["rr_target"],
                 s_oos["profit_factor"], s_oos["net_profit"], s_oos["max_drawdown"],
                 best_cfg["entry"]["type"], best_cfg["trading"]["rr_target"]), flush=True)
    print("\n=== AGREGAT OOS ===")
    print("  median PF: %.3f | positive folds: %d/%d | median net: %.0f"
          % (statistics.median(oos_pf), sum(1 for p in oos_pf if p >= 1.0),
             len(oos_pf), statistics.median(oos_net)))


def mode_oos(tune_sym):
    print("=== TUNE di %s, tes di semua simbol ===" % tune_sym)
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", tune_sym + "_M5.csv")
    bars = load_csv(path)[-80000:]
    base = apply_ticks(load_config(), tune_sym)
    best_cfg, best_net = select_best_params(bars, base, GRID)
    print("Best pada %s: type=%s rr=%.1f (net=%d)\n" % (
        tune_sym, best_cfg["entry"]["type"], best_cfg["trading"]["rr_target"], best_net))
    print("%-8s %6s %5s %5s %6s %8s %7s" % ("symbol", "bars", "trades", "win", "PF", "net", "DD"))
    results = []
    for p in data_files():
        s2 = sym_of(p)
        b = load_csv(p)
        c = apply_ticks(best_cfg, s2)
        st, *_ = run_backtest(b, c)
        results.append((s2, st))
        print("%-8s %6d %5d %5.3f %6.2f %8.0f %7.0f"
              % (s2, len(b), st["trades"], st["win_rate"], st["profit_factor"],
                 st["net_profit"], st["max_drawdown"]), flush=True)
    pf = [st["profit_factor"] for _, st in results]
    net = [st["net_profit"] for _, st in results]
    print("\n=== GATE ===")
    print("  PF median=%.3f | PF>=1.0 di %d/%d | net positif %d/%d"
          % (statistics.median(pf), sum(1 for x in pf if x >= 1.0), len(pf),
             sum(1 for x in net if x > 0), len(net)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["roll", "oos"], default="roll")
    ap.add_argument("--tune", default="EURUSD")
    args = ap.parse_args()
    if args.mode == "roll":
        mode_roll()
    else:
        mode_oos(args.tune)


if __name__ == "__main__":
    main()
