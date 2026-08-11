"""Live A/B report: analisis keputusan live dari SQLite.

Hitung per-engine (LLM vs deterministic):
  - total decisions, open rate, win rate, net, PF (1-bar forward return)
  - agreement action+bias
  - divergent list (LLM HOLD vs det OPEN, etc.)

Pemakaian di VPS:
  python -m reports.live_ab XAUUSD
  python -m reports.live_ab XAUUSD --since 200        # hanya N decision terbaru
  python -m reports.live_ab XAUUSD --csv data/XAUUSD_M5.csv
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import Memory
from config import load_config, resolve_path
from reports.compare_ab import TICKS


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _load_prices(csv_path):
    if not csv_path or not os.path.exists(csv_path):
        return {}
    from backtest.run import load_csv
    bars = load_csv(csv_path)
    return {b["time"]: i for i, b in enumerate(bars)}


def _forward_return(bars, idx, bars_index, fwd):
    j = min(idx + fwd, len(bars) - 1)
    if idx >= len(bars):
        return 0.0
    return bars[j]["close"] / bars[idx]["close"] - 1.0


def evaluate_engine(records, bars_index, bars, ts, tv, fwd, lot):
    stats = {"total": 0, "open": 0, "win": 0, "pnl": 0.0,
             "gw": 0.0, "gl": 0.0, "long": 0, "short": 0}
    for rec in records:
        stats["total"] += 1
        bias = rec.get("bias")
        action = rec.get("action")
        if action != "OPEN" or bias not in ("LONG", "SHORT"):
            continue
        idx = bars_index.get(rec.get("bar_time"))
        if idx is None:
            continue
        entry = rec.get("entry")
        sl = rec.get("sl")
        tp = rec.get("tp")
        if not entry or not sl or not tp:
            continue
        side = 1 if bias == "LONG" else -1
        if side == 1:
            stats["long"] += 1
        else:
            stats["short"] += 1
        exit_price = None
        for k in range(idx + 1, min(idx + 50, len(bars))):
            hi, lo = bars[k]["high"], bars[k]["low"]
            if side == 1:
                hit_sl = lo <= sl; hit_tp = hi >= tp
            else:
                hit_sl = hi >= sl; hit_tp = lo <= tp
            if hit_sl and hit_tp:
                exit_price = sl; break
            if hit_sl:
                exit_price = sl; break
            if hit_tp:
                exit_price = tp; break
        if exit_price is None:
            exit_price = bars[-1]["close"]
        pnl = side * (exit_price - entry) / ts * tv * lot
        stats["open"] += 1
        stats["pnl"] += pnl
        if pnl > 0:
            stats["win"] += 1
            stats["gw"] += pnl
        else:
            stats["gl"] += -pnl
    if stats["open"] > 0:
        stats["win_rate"] = stats["win"] / stats["open"]
        stats["pf"] = stats["gw"] / stats["gl"] if stats["gl"] > 0 else "inf"
    else:
        stats["win_rate"] = 0.0
        stats["pf"] = 0.0
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", default="XAUUSD", nargs="?")
    ap.add_argument("--since", type=int, default=300)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--fwd", type=int, default=30)
    ap.add_argument("--lot", type=float, default=0.1)
    args = ap.parse_args()

    cfg = load_config()
    mem = Memory(resolve_path(cfg, "memory"))
    rows = mem.conn.execute(
        "SELECT ts, engine, llm_decision, det_decision FROM decisions "
        "WHERE symbol=? ORDER BY ts DESC LIMIT ?", (args.symbol, args.since)).fetchall()

    if not rows:
        print(f"Tidak ada decision untuk {args.symbol}.")
        return

    csv_path = args.csv or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", f"{args.symbol}_M5.csv")
    bars_index = _load_prices(csv_path)
    bars = []
    if bars_index:
        from backtest.run import load_csv
        bars = load_csv(csv_path)
    ts, tv = TICKS.get(args.symbol, (1e-5, 1.0))

    per_engine = {"llm": [], "det": []}
    for _ts, engine, llm_j, det_j in rows:
        if engine == "llm" and llm_j:
            per_engine["llm"].append(json.loads(llm_j))
        if det_j:
            per_engine["det"].append(json.loads(det_j))
        if engine == "deterministic" and llm_j:
            per_engine["det"].append(json.loads(llm_j))

    if not bars:
        print(f"CSV {csv_path} tidak ada. Forward fill replay dilewati.")
        print(f"  llm: {len(per_engine['llm'])}  det: {len(per_engine['det'])}")
        return

    results = {}
    for eng in ("llm", "det"):
        stats = evaluate_engine(per_engine[eng], bars_index, bars, ts, tv,
                                args.fwd, args.lot)
        results[eng] = stats

    agree = total = 0
    divergent = []
    for _ts, engine, llm_j, det_j in rows:
        if not llm_j or not det_j:
            continue
        try:
            llm_d = json.loads(llm_j); det_d = json.loads(det_j)
        except Exception:
            continue
        total += 1
        same = llm_d.get("action") == det_d.get("action")
        if same:
            agree += 1
        else:
            divergent.append({"ts": _ts,
                               "llm_action": llm_d.get("action"),
                               "det_action": det_d.get("action"),
                               "llm_bias": llm_d.get("bias"),
                               "det_bias": det_d.get("bias")})

    print(f"=== Live A/B {args.symbol} ({len(rows)} decision, {len(bars)} bar) ===")
    print(f"{'engine':<14} {'total':>5} {'open':>5} {'win%':>6} "
          f"{'net':>10} {'PF':>6} {'long':>5} {'short':>5}")
    for eng in ("llm", "det"):
        s = results[eng]
        print(f"{eng:<14} {s['total']:>5} {s['open']:>5} {s['win_rate']*100:>5.1f}% "
              f"{s['pnl']:>10.2f} {s['pf']:>6.2f} {s['long']:>5} {s['short']:>5}")

    print(f"\nAgreement: {agree}/{total} = {agree/total*100:.1f}%" if total else "\nAgreement: n/a")
    if divergent:
        print(f"\nDivergent (action berbeda) — 5 terbaru:")
        for d in divergent[:5]:
            print(f"  ts={d['ts']:.0f} llm={d['llm_action']}/{d['llm_bias']} "
                  f"det={d['det_action']}/{d['det_bias']}")


if __name__ == "__main__":
    main()