"""Feeder A/B: kirim data M5 live ke bridge tiap close bar (signal-only).

Menggantikan peran EA utk pengumpulan data A/B: tiap M5 bar baru close,
tarik bars multi-TF dari MT5 (package MetaTrader5) lalu POST ke /decision.
Bridge akan menghitung & log kedua engine (llm vs deterministic) ke SQLite.

Pemakaian:
  python feeder.py --symbols XAUUSD,EURUSD
  python feeder.py --symbols EURUSD --once       # tes sekali, langsung keluar
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BRIDGE = "http://127.0.0.1:8080/decision"
BARS = 60
TFS = ["M5", "M15", "H1", "H4"]

_last_bar = {}


def tf_const(name):
    import MetaTrader5 as mt5
    return {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}[name]


def pull_chrono(symbol, tf_name, count):
    import MetaTrader5 as mt5
    rates = mt5.copy_rates_from_pos(symbol, tf_const(tf_name), 0, count)
    if rates is None or len(rates) == 0:
        return None
    # copy_rates_from_pos: index 0 = bar terlama (ascending)
    return {
        "t": [int(r["time"]) for r in rates],
        "o": [round(r["open"], 5) for r in rates],
        "h": [round(r["high"], 5) for r in rates],
        "l": [round(r["low"], 5) for r in rates],
        "c": [round(r["close"], 5) for r in rates],
        "v": [int(r["tick_volume"]) for r in rates],
    }


def build_payload(symbol, last_time):
    import MetaTrader5 as mt5
    info = mt5.symbol_info(symbol)
    ai = mt5.account_info()
    tick_size = info.trade_tick_size if info else 1e-5
    tick_value = info.trade_tick_value if info else 1.0
    ask = mt5.symbol_info_tick(symbol).ask if mt5.symbol_info_tick(symbol) else 0.0
    bid = mt5.symbol_info_tick(symbol).bid if mt5.symbol_info_tick(symbol) else 0.0
    bars = {}
    for tf_name in TFS:
        b = pull_chrono(symbol, tf_name, BARS)
        if b:
            bars[tf_name] = b
    return {
        "symbol": symbol,
        "timeframe": "M5",
        "bars": bars,
        "positions": [],
        "account": {
            "balance": ai.balance if ai else 10000.0,
            "equity": ai.equity if ai else 10000.0,
            "tick_size": tick_size,
            "tick_value": tick_value,
            "spread": max(ask - bid, 0.0002) if (ask and bid) else 0.0002,
        },
        "server_time": last_time,
    }


def post(symbol, last_time):
    payload = build_payload(symbol, last_time)
    req = urllib.request.Request(BRIDGE, data=json.dumps(payload).encode(), method="POST")
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        c = resp.get("compare", {})
        print("%s bar=%s engine=%s action=%s | llm=%s det=%s lat=%sms"
              % (symbol, time.strftime("%H:%M", time.localtime(last_time)),
                 resp.get("engine"), resp.get("action"),
                 (c.get("llm") or {}).get("action"), (c.get("det") or {}).get("action"),
                 resp.get("latency_ms")), flush=True)
    except Exception as e:
        print("POST fail %s: %s" % (symbol, e), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="XAUUSD,EURUSD")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("ERROR: MT5 tidak terhubung."); sys.exit(1)

    if args.once:
        for sym in symbols:
            mt5.symbol_select(sym, True)
            r = mt5.copy_rates_from_pos(sym, 5, 0, 1)
            if r is not None:
                post(sym, int(r[-1]["time"]))
        mt5.shutdown()
        return

    print("Feeder jalan. Menunggu bar M5 baru untuk:", symbols, flush=True)
    while True:
        for sym in symbols:
            mt5.symbol_select(sym, True)
            r = mt5.copy_rates_from_pos(sym, 5, 0, 1)
            if r is None:
                continue
            t = int(r[-1]["time"])
            if _last_bar.get(sym) != t:
                _last_bar[sym] = t
                post(sym, t)
        time.sleep(20)


if __name__ == "__main__":
    main()
