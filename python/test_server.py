"""Uji server bridge end-to-end via TestClient + MockLLM."""
import os, sys, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
import server
from mock import MockLLM
from test_pipeline import gen_bars

# pastikan test deterministik & cepat: nonaktifkan compare (yang memicu LLM
# live tiap request), pakai MockLLM, dan mode deterministic default.
server.CFG["ab_test"]["compare"] = False
server.LLM = MockLLM(server.CFG["llm"])

client = TestClient(server.app)
client.post("/mode", json={"engine": "auto"})

def payload(symbol, drift, vol, seed, positions=None, account=None):
    bars = {}
    tf_n = {"M5": 120, "M15": 100, "H1": 80, "H4": 60}
    for tf, n in tf_n.items():
        b = gen_bars(n=n, drift=drift, vol=vol, seed=seed)
        bars[tf] = {"t": list(range(n)), "o": b["open"].round(5).tolist(),
                    "h": b["high"].round(5).tolist(), "l": b["low"].round(5).tolist(),
                    "c": b["close"].round(5).tolist(), "v": b["volume"].round(2).tolist()}
    return {"symbol": symbol, "timeframe": "M5", "bars": bars,
            "positions": positions or [],
            "account": account or {"balance": 10000, "equity": 10050,
                                   "tick_size": 1e-5, "tick_value": 1.0},
            "server_time": 1700000000}

r = client.get("/health")
print("HEALTH:", r.json())

r = client.post("/decision", json=payload("EURUSD", drift=0.0012, vol=0.0003, seed=3))
d = r.json()
print("TREND RESPONSE:", json.dumps(d, indent=1))

r = client.post("/decision", json=payload("GBPUSD", drift=0.0, vol=0.00015, seed=4))
print("RANGE RESPONSE:", r.json()["action"], r.json()["bias"], r.json()["regime"]["label"])

r = client.post("/mode", json={"engine": "deterministic"})
print("FORCE MODE:", r.json())
r = client.post("/decision", json=payload("EURUSD", drift=0.0012, vol=0.0003, seed=3))
print("DETERMINISTIC:", r.json()["engine"], r.json()["action"], r.json()["lot"])

r = client.post("/mode", json={"engine": "llm"})
# paksa mock deterministik (bukan LLM live) untuk uji alur llm-path
import server as _srv
from mock import MockLLM
_srv.LLM = MockLLM(_srv.CFG["llm"])
r = client.post("/decision", json=payload("EURUSD", drift=0.0012, vol=0.0003, seed=3))
d = r.json()
print("MOCK-LLM:", d["engine"], d["action"], d["bias"], "sl", d["sl"], "tp", d["tp"])
assert d["engine"] == "llm" and d["action"] == "OPEN" and d["bias"] == "LONG"

r = client.post("/mode", json={"engine": "auto"})
r = client.get("/history", params={"symbol": "EURUSD", "limit": 5})
print("HISTORY rows:", len(r.json()["decisions"]))
