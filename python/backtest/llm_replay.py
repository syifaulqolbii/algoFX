"""LLM replay tuning: evaluasi keputusan LLM pada data historis XAUUSD.

Pre-filter bar TREND (regime deterministik) secara lokal, sample, kirim ke
bridge /decision (dengan override prompt), lalu evaluasi:
  - open-rate & win-rate (fill replay SL/TP di bar berikutnya, lot tetap)
  - directional accuracy (bias vs return fwd bar ke depan)
  - agreement vs engine deterministik

Pemakaian:
  python -m backtest.llm_replay --symbol XAUUSD --prompt prompts/decision.md
  python -m backtest.llm_replay --symbol XAUUSD --prompt prompts/decision_v2_action.md --bars 15000 --step 15 --limit 20
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from backtest.engine import (_fast_feat, _ohlcv_of, resample, rolling_rank,
                             rolling_vol_ratio, compute_series)
from backtest.run import load_csv
from config import load_config
from regime import classify
from reports.compare_ab import simulate, TICKS

BRIDGE = "http://127.0.0.1:8080/decision"
BARS_PER_TF = 60
BASE_BARS = {"M15": 3, "H1": 12, "H4": 48}
WINDOW = 60


def build_payload(symbol, m5_rows, htf_bars, ts, tv, spread, bar_time):
    def to_arr(rows, key, digits=5):
        return [round(r[key], digits) for r in rows]
    bars = {}
    for name, rows in [("M5", m5_rows)] + [(n, htf_bars[n]) for n in BASE_BARS]:
        if len(rows) >= 60:
            bars[name] = {
                "t": [int(r["time"]) for r in rows],
                "o": to_arr(rows, "open"), "h": to_arr(rows, "high"),
                "l": to_arr(rows, "low"), "c": to_arr(rows, "close"),
                "v": [int(r["volume"]) for r in rows],
            }
    return {
        "symbol": symbol, "timeframe": "M5", "bars": bars, "positions": [],
        "account": {"balance": 10000.0, "equity": 10000.0,
                    "tick_size": ts, "tick_value": tv, "spread": spread},
        "server_time": bar_time,
    }


def post(payload, prompt, temperature, max_tokens):
    body = dict(payload)
    if prompt:
        body["prompt"] = prompt
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    body["log"] = False
    req = urllib.request.Request(BRIDGE, data=json.dumps(body).encode(), method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def local_regime_bars(bars, rc, ecfg, cache_path=None):
    """Kembalikan daftar (i, regime_label) untuk bar TREND (parity deterministik).

    Hasil di-cache ke file agar scan 15k bar × 4 TF dihitung sekali saja untuk
    semua varian prompt.
    """
    if cache_path and os.path.exists(cache_path):
        try:
            data = json.load(open(cache_path))
            return [(int(a), b) for a, b in data]
        except Exception:
            pass
    t0 = time.time()
    n = len(bars)
    m5 = _ohlcv_of(bars)
    m5_series = compute_series(m5, rc)
    rank = rolling_rank(m5_series["atr_pct"], WINDOW)
    volr = rolling_vol_ratio(m5_series["volume"], 20)
    htf = {}
    for tf, every in BASE_BARS.items():
        s = compute_series(_ohlcv_of(resample(bars, every)), rc)
        htf[tf] = (s, rolling_rank(s["atr_pct"], WINDOW), rolling_vol_ratio(s["volume"], 20))
    swing_n = int(ecfg.get("swing_n", 20))
    lb = int(ecfg.get("breakout_lookback", 3))
    ep = float(ecfg.get("pullback_ema_dist_pct", 0.15))
    out = []
    for i in range(60, n - 60):
        feats = {}
        f = _fast_feat(m5_series, rank, volr, i, WINDOW, swing_n, lb, ep)
        if f:
            feats["M5"] = f
        for tf in BASE_BARS:
            # hanya bar HTF yang SELESAI (hindari lookahead, parity engine.py)
            idx = (i + 1) // BASE_BARS[tf] - 1
            if idx >= 0:
                s, r_, v_ = htf[tf]
                g = _fast_feat(s, r_, v_, min(idx, len(s["close"]) - 1), WINDOW, swing_n, lb, ep)
                if g:
                    feats[tf] = g
        if "M5" not in feats:
            continue
        reg = classify(feats["M5"], {t: feats[t] for t in BASE_BARS if t in feats}, rc)
        if reg.label in ("TREND_UP", "TREND_DOWN"):
            out.append((i, reg.label))
        if (i - 60) % 2000 == 0:
            print(f"    scan {i}/{n} trend={len(out)}", flush=True)
    print(f"    scan selesai dalam {time.time()-t0:.0f}s, bar trend={len(out)}", flush=True)
    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump([[i, lbl] for i, lbl in out], f)
    return out


def evaluate(decisions, bars, fwd, lot, ts, tv):
    """decisions: list (i, engine, decision). engine in ('llm','det')."""
    res = {"opens": 0, "wins": 0, "pnl": 0.0, "gw": 0.0, "gl": 0.0,
           "acted": 0, "acted_correct": 0, "skipped": 0}
    for i, eng, d in decisions:
        if not d:
            continue
        bias = d.get("bias")
        if bias == "FLAT":
            res["skipped"] += 1
            continue
        # directional accuracy vs forward return
        j = min(i + fwd, len(bars) - 1)
        fwd_ret = bars[j]["close"] / bars[i]["close"] - 1.0
        res["acted"] += 1
        if (bias == "LONG" and fwd_ret > 0) or (bias == "SHORT" and fwd_ret < 0):
            res["acted_correct"] += 1
        # fill replay utk OPEN
        pnl = simulate(d, bars, i, lot, ts, tv)
        if pnl is None:
            continue
        res["opens"] += 1
        res["pnl"] += pnl
        if pnl > 0:
            res["wins"] += 1
            res["gw"] += pnl
        else:
            res["gl"] += -pnl
    res["win_rate"] = res["wins"] / res["opens"] if res["opens"] else 0.0
    res["pf"] = res["gw"] / res["gl"] if res["gl"] > 0 else float("inf")
    res["dir_acc"] = res["acted_correct"] / res["acted"] if res["acted"] else 0.0
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--bars", type=int, default=15000)
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--fwd", type=int, default=30)
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--lot", type=float, default=0.1)
    ap.add_argument("--fresh", action="store_true", help="mulai ulang (hapus keputusan lama)")
    ap.add_argument("--rebuild-cache", action="store_true", help="paksa rebuild cache regime")
    args = ap.parse_args()

    cfg = load_config()
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", f"{args.symbol}_M5.csv")
    bars = load_csv(csv_path)[-args.bars:]
    ts, tv = TICKS.get(args.symbol, (1e-5, 1.0))
    spread = 0.30 if args.symbol == "XAUUSD" else 0.0002

    cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "reports", f"cache_{args.symbol}_{args.bars}.json")
    if args.rebuild_cache and os.path.exists(cache_path):
        os.remove(cache_path)
    trend = local_regime_bars(bars, cfg["regime"], cfg.get("entry", {}), cache_path)
    sampled = trend[::args.step]
    if args.limit:
        sampled = sampled[:args.limit]
    print(f"Simbol {args.symbol}: {len(bars)} bar | bar trend={len(trend)} | sample={len(sampled)}", flush=True)

    decs = {"llm": [], "det": []}
    tag = os.path.splitext(os.path.basename(args.prompt or "decision"))[0]
    dec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reports", f"decisions_{args.symbol}_{tag}.jsonl")
    done = set()
    if os.path.exists(dec_path):
        with open(dec_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                done.add(d["i"])
                decs["llm"].append((d["i"], "llm", d.get("llm")))
                decs["det"].append((d["i"], "det", d.get("det")))
        print(f"Resume: {len(done)} bar sudah diproses", flush=True)

    fout = open(dec_path, "w") if args.fresh else open(dec_path, "a")
    for k, (i, reg_label) in enumerate(sampled):
        if i in done:
            continue
        m5_rows = bars[max(0, i - BARS_PER_TF + 1):i + 1]
        htf_bars = {}
        for tf, every in BASE_BARS.items():
            full = resample(bars[:i + 1], every)
            htf_bars[tf] = full[-BARS_PER_TF:]
        payload = build_payload(args.symbol, m5_rows, htf_bars, ts, tv, spread, bars[i]["time"])
        try:
            resp = post(payload, args.prompt, args.temperature, args.max_tokens)
        except Exception as e:
            print(f"  [{k+1}/{len(sampled)}] bar {i} POST error: {e}", flush=True)
            continue
        c = resp.get("compare") or {}
        line = {"i": i, "llm": c.get("llm"), "det": c.get("det")}
        fout.write(json.dumps(line) + "\n")
        fout.flush()
        decs["llm"].append((i, "llm", c.get("llm")))
        decs["det"].append((i, "det", c.get("det")))
        if (k + 1) % 25 == 0 or k == len(sampled) - 1:
            print(f"  [{k+1}/{len(sampled)}] selesai bar {i}", flush=True)
        time.sleep(0.1)
    fout.close()

    print("\n=== HASIL REPLAY ===")
    print("%-4s %5s %7s %6s %6s %9s %6s %7s %6s" %
          ("eng", "dec", "opens", "open%", "win%", "net", "PF", "dirAcc", "skip"))
    results = {}
    for eng in ("llm", "det"):
        r = evaluate(decs[eng], bars, args.fwd, args.lot, ts, tv)
        results[eng] = r
        open_rate = r["opens"] / len(decs[eng]) if decs[eng] else 0.0
        print("%-4s %5d %7d %6.1f %6.1f %9.1f %6.2f %7.2f %6d" %
              (eng, len(decs[eng]), r["opens"], open_rate * 100, r["win_rate"] * 100,
               r["pnl"], r["pf"], r["dir_acc"], r["skipped"]))

    out = {
        "symbol": args.symbol, "bars": len(bars), "sample": len(sampled),
        "prompt": args.prompt, "temperature": args.temperature,
        "results": results,
    }
    tag = os.path.splitext(os.path.basename(args.prompt or "decision"))[0]
    rep_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reports", f"llm_replay_{args.symbol}_{tag}.json")
    os.makedirs(os.path.dirname(rep_path), exist_ok=True)
    with open(rep_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSimpan: {rep_path}")


if __name__ == "__main__":
    main()
