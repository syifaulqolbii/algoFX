"""Pull data M5 historis dari MT5 ke CSV untuk backtest.

Pemakaian (jalankan saat MT5 sudah login):
  python -m backtest.pull_data --symbol EURUSD --bars 100000
  python -m backtest.pull_data --symbol EURUSD --days 60

Output: python/data/<SYMBOL>_M5.csv (time,open,high,low,close,volume)
Format time: UNIX epoch detik.
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--bars", type=int, default=100000)   # M5, ~100k bar = ~12 bulan
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("ERROR: MT5 tidak terhubung. Pastikan terminal sudah login akun.")
        sys.exit(1)
    ti = mt5.terminal_info(); ai = mt5.account_info()
    if not (ti and ti.connected and ai):
        print("ERROR: MT5 belum login/terhubung.")
        mt5.shutdown(); sys.exit(1)
    print(f"Connected: login={ai.login} server={ai.server} demo={ai.trade_mode==1}")

    symbol = args.symbol
    info = mt5.symbol_info(symbol)
    if not info:
        print(f"ERROR: simbol {symbol} tidak tersedia di Market Watch.")
        mt5.shutdown(); sys.exit(1)
    digits = info.digits
    mt5.symbol_select(symbol, True)   # pastikan aktif & tersinkron

    if args.days:
        bars = args.days * 24 * 60 // 5
    else:
        bars = args.bars

    # pull ber-chunk (terminal punya batas per panggilan)
    CHUNK = 50000
    rates = []
    pos = 0
    while len(rates) < bars:
        n = min(CHUNK, bars - len(rates))
        chunk = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, pos, n)
        if chunk is None or len(chunk) == 0:
            print(f"WARNING: stop di {len(rates)} bar. {mt5.last_error()}")
            break
        rates.extend(chunk)
        pos += len(chunk)
    if not rates:
        print("ERROR: tidak ada data.")
        mt5.shutdown(); sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, f"{symbol}_M5.csv")

    # urutkan kronologis & dedupe (order hasil chunk tidak dijamin)
    seen = set()
    rows = []
    for r in sorted(rates, key=lambda x: int(x["time"])):
        t = int(r["time"])
        if t in seen:
            continue
        seen.add(t)
        rows.append([
            t,
            round(r["open"], digits), round(r["high"], digits),
            round(r["low"], digits), round(r["close"], digits),
            int(r["tick_volume"]),
        ])

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    mt5.shutdown()
    print(f"OK: {len(rows)} bar M5 -> {out}")
    print(f"Rentang: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rows[0][0]))} "
          f"s/d {time.strftime('%Y-%m-%d %H:%M', time.gmtime(rows[-1][0]))}")


if __name__ == "__main__":
    main()
