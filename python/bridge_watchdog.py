"""Watchdog bridge: pastikan bridge selalu hidup.

Cek /health tiap interval; bila N kali gagal berturut-turut, restart uvicorn.
Pemakaian (background):
  python -u bridge_watchdog.py
"""
import os
import subprocess
import sys
import time
import urllib.request

_HEALTH = "http://127.0.0.1:8080/health"
_INTERVAL = 30          # detik antar cek
_MAX_FAIL = 3           # N gagal berturut -> restart
_BASE = os.path.dirname(os.path.abspath(__file__))


def health_ok():
    try:
        with urllib.request.urlopen(_HEALTH, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def start_bridge():
    log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "bridge_watchdog.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    out = open(log, "a")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_BASE, "server.py")],
        cwd=_BASE, stdout=out, stderr=out)
    print("%s bridge started pid=%s" % (time.strftime("%H:%M:%S"), proc.pid), flush=True)
    return proc


def main():
    fails = 0
    proc = None
    print("Watchdog jalan (interval %ds, max_fail %d)" % (_INTERVAL, _MAX_FAIL), flush=True)
    while True:
        if health_ok():
            fails = 0
        else:
            fails += 1
            print("%s health fail %d/%d" % (time.strftime("%H:%M:%S"), fails, _MAX_FAIL), flush=True)
            if fails >= _MAX_FAIL:
                if proc is not None:
                    print("membunuh bridge lama", flush=True)
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        proc.kill()
                proc = start_bridge()
                time.sleep(5)
                fails = 0
        time.sleep(_INTERVAL)


if __name__ == "__main__":
    main()
