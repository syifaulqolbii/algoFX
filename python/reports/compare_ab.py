"""A/B replay: bandingkan keputusan LLM vs deterministik dari memory SQLite.

Setiap keputusan OPEN direplay sebagai satu trade dengan lot tetap (0.1) di bar
berikutnya (SL/TP hit intra-bar, asumsi konservatif SL didahulukan). Butuh CSV
M5 simbol tsb untuk bar lanjutan (data/<SYMBOL>_M5.csv).

Pemakaian:
  python -m reports.compare_ab --symbol EURUSD
  python -m reports.compare_ab --symbol EURUSD --lot 0.1

Agar data terkumpul: aktifkan ab_test.compare=true di config.yaml, pasang EA
dengan InpSignalOnly=true selama beberapa hari.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import Memory
from config import load_config, resolve_path
from backtest.run import load_csv

TICKS = {
    "EURUSD": (1e-5, 1.0), "GBPUSD": (1e-5, 1.0), "AUDUSD": (1e-5, 1.0),
    "USDCAD": (1e-5, 1.0), "USDCHF": (1e-5, 1.0), "USDJPY": (0.01, 6.5),
    "XAUUSD": (0.01, 1.0),
}


def simulate(decision, bars, start_idx, lot, tick_size, tick_value):
    """Replay satu keputusan OPEN dari bar start_idx. return pnl atau None."""
    if not decision or decision.get("action") != "OPEN":
        return None
    side = 1 if decision.get("bias") == "LONG" else -1
    entry = decision.get("entry")
    sl = decision.get("sl")
    tp = decision.get("tp")
    if not entry or not sl or not tp:
        return None
    for k in range(start_idx + 1, len(bars)):
        hi, lo = bars[k]["high"], bars[k]["low"]
        if side == 1:
            hit_sl = lo <= sl
            hit_tp = hi >= tp
        else:
            hit_sl = hi >= sl
            hit_tp = lo <= tp
        if hit_sl and hit_tp:
            exit_p = sl
            break
        elif hit_sl:
            exit_p = sl
            break
        elif hit_tp:
            exit_p = tp
            break
    else:
        exit_p = bars[-1]["close"]
    return side * (exit_p - entry) / tick_size * tick_value * lot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--lot", type=float, default=0.1)
    args = ap.parse_args()

    cfg = load_config()
    mem = Memory(resolve_path(cfg, "memory"))
    rows = mem.ab_rows(args.symbol)
    if not rows:
        print(f"Tidak ada data A/B utk {args.symbol}. Jalankan dulu dengan ab_test.compare=true.")
        return
    data_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(data_dir, "data", f"{args.symbol}_M5.csv")
    bars = load_csv(csv_path) if os.path.exists(csv_path) else []
    times = {b["time"]: i for i, b in enumerate(bars)}
    ts, tv = TICKS.get(args.symbol, (1e-5, 1.0))

    stats = defaultdict(lambda: {"opens": 0, "wins": 0, "pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0})
    agree = total = 0
    no_price = 0
    for r in rows:
        st = (r["payload"] or {}).get("server_time")
        if st not in times:
            no_price += 1
            continue
        idx = times[st]
        for eng in ("llm", "det"):
            pnl = simulate(r[eng], bars, idx, args.lot, ts, tv)
            if pnl is None:
                continue
            s = stats[eng]
            s["opens"] += 1
            s["pnl"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["gross_win"] += pnl
            else:
                s["gross_loss"] += -pnl
        # agreement action
        if r["llm"] and r["det"]:
            total += 1
            if r["llm"].get("action") == r["det"].get("action"):
                agree += 1

    print(f"=== A/B {args.symbol} | {len(rows)} bar | lot={args.lot} | no_price={no_price} ===")
    for eng in ("det", "llm"):
        s = stats[eng]
        pf = s["gross_win"] / s["gross_loss"] if s["gross_loss"] > 0 else float("inf")
        wr = s["wins"] / s["opens"] if s["opens"] else 0.0
        print(f"  {eng:>4s}: opens={s['opens']:>4} win={wr:.3f} net={s['pnl']:>9.2f} "
              f"PF={pf:>5.2f} grossWin={s['gross_win']:>9.2f} grossLoss={s['gross_loss']:>9.2f}")
    print(f"  agreement (action sama): {agree}/{total} = {agree / total:.2f}" if total else "  no paired decisions")


if __name__ == "__main__":
    main()
