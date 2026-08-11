"""Bridge HTTP lokal antara EA MT5 dan mesin keputusan (LLM/deterministik).

Endpoint:
  GET  /health            status engine + kesiapan LLM
  POST /decision          kirim bars OHLCV multi-TF -> dapatkan sinyal trading
  GET  /history           riwayat keputusan (symbol, limit)
  GET  /mode              mode engine saat ini
  POST /mode              paksa engine: auto | llm | deterministic

Payload /decision (dari EA):
{
  "symbol": "EURUSD",
  "timeframe": "M5",
  "request_id": "abc",
  "bars": {"M5": {"t":[], "o":[], "h":[], "l":[], "c":[], "v":[]}, ...},
  "positions": [{"type":0,"lots":0.1,"open_price":1.1,"sl":0,"tp":0,"profit":1.2}],
  "account": {"balance":10000,"equity":10050,"tick_size":1e-5,"tick_value":1.0},
  "server_time": 1234
}
"""
from __future__ import annotations

import json
import logging
import os
import sys
import secrets
import time
import uuid
import csv as _csv_mod
from pathlib import Path

import numpy as np
from starlette.concurrency import run_in_threadpool

# Proactor loop default di Windows rawan bug "Accept failed / WinError 64"
# di bawah beban koneksi. Selector loop lebih stabil. Dipasang di import agar
# berlaku TANPA MEMANDANG cara start (python server.py | uvicorn server:app).
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
import uvicorn

from config import ensure_data_dir, load_config, resolve_path
from features import compute_features, multi_tf_snapshot
from llm import LLMClient, LLMError, ValidationError
from memory import Memory
from mock import MockLLM
from regime import classify, deterministic_signal, compute_lot
from reports.compare_ab import TICKS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridge")

app = FastAPI(title="Regime Bridge", version="0.1.0")

STATE = {"engine": "auto", "forced": False}
FAILURES = {}
BAR_CSV = {}   # symbol -> {"path": str, "times": set[int]} utk auto-append bar live


def _init_bar_csv(symbol):
    """Siapkan pelacakan CSV M5: muat waktu bar yang sudah ada."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", f"{symbol}_M5.csv")
    times = set()
    try:
        with open(path, newline="") as f:
            r = _csv_mod.DictReader(f)
            for row in r:
                times.add(int(float(row["time"])))
    except FileNotFoundError:
        times = set()
    BAR_CSV[symbol] = {"path": path, "times": times}


def _append_live_bars(symbol, bars):
    """Append bar M5 baru dari request EA ke CSV live (dedupe by time)."""
    entry = BAR_CSV.get(symbol)
    if not entry or not bars.get("M5"):
        return
    t = bars["M5"].get("t") or []
    o = bars["M5"].get("o") or []
    h = bars["M5"].get("h") or []
    l = bars["M5"].get("l") or []
    c = bars["M5"].get("c") or []
    v = bars["M5"].get("v") or []
    new_rows = []
    for i in range(len(t)):
        bt = int(t[i])
        if bt in entry["times"]:
            continue
        try:
            new_rows.append([bt,
                             round(float(o[i]), 5), round(float(h[i]), 5),
                             round(float(l[i]), 5), round(float(c[i]), 5),
                             int(v[i])])
        except (TypeError, ValueError):
            continue
    if not new_rows:
        return
    try:
        newfile = not os.path.exists(entry["path"])
        with open(entry["path"], "a", newline="") as f:
            w = _csv_mod.writer(f)
            if newfile:
                w.writerow(["time", "open", "high", "low", "close", "volume"])
            for row in new_rows:
                w.writerow(row)
                entry["times"].add(row[0])
    except Exception as e:
        log.warning("append live bars %s gagal: %s", symbol, e)


class Account(BaseModel):
    balance: float = 10000.0
    equity: float = 10000.0
    tick_size: float = 1e-5
    tick_value: float = 1.0
    spread: float = 0.0002


class Position(BaseModel):
    type: int = 0          # 0=BUY 1=SELL (MT5)
    lots: float = 0.0
    open_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0


class DecisionRequest(BaseModel):
    symbol: str
    timeframe: str = "M5"
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    bars: dict
    positions: list[Position] = []
    account: Account = Account()
    server_time: float = 0.0
    prompt: str | None = None        # override system prompt (A/B tuning)
    temperature: float | None = None # override suhu LLM
    max_tokens: int | None = None    # override budget token
    log: bool = True                 # false = jangan tulis ke SQLite (replay)
    bridge_token: str | None = None


def _to_np(values):
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        return None
    return arr


def _build_snapshot(req, features_by_tf, regime):
    tf_order = list(req.bars.keys())
    snap = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "server_time": int(req.server_time),
        "account": {"balance": req.account.balance, "equity": req.account.equity},
        "features": multi_tf_snapshot(features_by_tf, tf_order),
        "regime": regime.to_dict(),
        "open_positions": [p.model_dump() for p in req.positions],
        "recent": _recent_context(req.symbol),
    }
    return json.dumps(snap, ensure_ascii=False)


def _recent_context(symbol, limit=5):
    try:
        return MEM.recent(symbol, limit=limit)
    except Exception:
        return []


def _pick_engine(symbol):
    """Pilih engine: llm bila sehat & tersedia, selain itu deterministic."""
    if STATE["forced"]:
        return STATE["engine"]
    cfg = CFG["llm"]
    if not cfg.get("ready"):
        return "deterministic"
    fails = FAILURES.get(symbol, 0)
    if fails >= cfg.get("fallback_after_failures", 3):
        return "deterministic"
    return "llm"


def _run_decision(req):
    symbol = req.symbol
    cfg = CFG
    if cfg.get("bridge", {}).get("require_token", False):
        expected = cfg.get("bridge", {}).get("token", "")
        if not expected or not req.bridge_token or not secrets.compare_digest(req.bridge_token, expected):
            return {"error": "invalid bridge token", "request_id": req.request_id,
                    "action": "HOLD", "bias": "FLAT", "engine": "auth_error"}
    rc_full = {**cfg["regime"], **cfg.get("entry", {})}

    features_by_tf = {}
    for tf, arrs in req.bars.items():
        o = _to_np(arrs.get("o")); h = _to_np(arrs.get("h"))
        l = _to_np(arrs.get("l")); c = _to_np(arrs.get("c")); v = _to_np(arrs.get("v"))
        if any(x is None for x in (o, h, l, c, v)) or not (len(o) == len(h) == len(l) == len(c) == len(v)):
            continue
        f = compute_features({"open": o, "high": h, "low": l, "close": c, "volume": v},
                             rc=rc_full)
        if f:
            features_by_tf[tf] = f

    tf_order = list(req.bars.keys())
    if not tf_order or not features_by_tf.get(tf_order[0]):
        return {"error": "insufficient bars", "request_id": req.request_id}

    primary = features_by_tf[tf_order[0]]
    higher = {tf: features_by_tf[tf] for tf in tf_order[1:]}
    regime = classify(primary, higher, cfg["regime"])

    spread = req.account.spread if req.account.spread and req.account.spread > 0 else 0.0002
    engine = _pick_engine(symbol)
    response = None
    error = None
    decision = None

    compare = CFG.get("ab_test", {}).get("compare", False)
    want_llm = engine == "llm" or (compare and cfg["llm"].get("ready"))
    det_dec = llm_dec = None
    llm_resp = det_resp = None
    decision = None

# deterministik selalu dihitung (murah) utk baseline A/B
    det_dec = deterministic_signal(features_by_tf, tf_order, regime,
                                    req.account.model_dump(), cfg, spread,
                                    bar_time=req.server_time or time.time())

    if want_llm:
        try:
            snap = _build_snapshot(req, features_by_tf, regime)
            if req.prompt:
                system = req.prompt
            else:
                pname = cfg["llm"].get("prompt_file", "decision.md")
                system = (Path(__file__).resolve().parents[1] / "prompts" / pname).read_text(encoding="utf-8")
            user = ("Snapshot pasar (semua fitur dihitung dari OHLCV mentah, "
                    "angka harga 5 desimal). Keputusan:\n" + snap)
            llm_dec = LLM.decide(system, user, temperature=req.temperature,
                                 max_tokens=req.max_tokens)
            if not LLM.llm_acceptable(llm_dec, regime):
                log.info("LLM decision rejected by gate -> HOLD (%s)", symbol)
                llm_dec = {
                    "action": "HOLD", "bias": "FLAT", "confidence": llm_dec["confidence"],
                    "lot_fraction": 0.01, "entry": None, "sl": None, "tp": None,
                    "regime_label": regime.label, "reasoning": "gate: " + llm_dec["reasoning"],
                }
            FAILURES[symbol] = 0
            decision = llm_dec
        except (LLMError, ValidationError) as e:
            error = str(e)
            FAILURES[symbol] = FAILURES.get(symbol, 0) + 1
            log.warning("LLM fail(%d) %s: %s", FAILURES[symbol], symbol, error)
            if FAILURES[symbol] >= cfg["llm"].get("fallback_after_failures", 3):
                engine = "deterministic"
                log.info("Switching to deterministic for %s", symbol)

    if decision is None:
        decision = det_dec
        engine = "deterministic"

    llm_resp = _finalize(llm_dec, regime, req.account, primary, "llm") if llm_dec else None
    det_resp = _finalize(det_dec, regime, req.account, primary, "deterministic")
    bar_time = int(req.server_time) if req.server_time else int(time.time())
    if llm_resp is not None:
        llm_resp["bar_time"] = bar_time
    if det_resp is not None:
        det_resp["bar_time"] = bar_time
    response = llm_resp if engine == "llm" else det_resp

    if req.log:
        MEM.log_decision(symbol, req.timeframe, engine, regime.to_dict(),
                         response["action"], response["bias"], req.request_id,
                         _build_snapshot(req, features_by_tf, regime), response, error,
                         llm_decision=llm_resp, det_decision=det_resp)
    response["request_id"] = req.request_id
    response["regime"] = regime.to_dict()
    if compare:
        response["compare"] = {
            "llm": {k: llm_resp.get(k) for k in ("action", "bias", "confidence", "reasoning", "entry", "sl", "tp", "lot")} if llm_resp else None,
            "det": {k: det_resp.get(k) for k in ("action", "bias", "reasoning", "entry", "sl", "tp", "lot")} if det_resp else None,
        }
    # simpan bar M5 live ke CSV utk forward-replay (always up-to-date)
    _append_live_bars(symbol, req.bars)
    return response


def _finalize(decision, regime, account, primary, engine):
    tcfg = CFG.get("trading", {})
    risk_cfg = CFG.get("risk", {})
    lot = 0.0
    entry, sl, tp = decision.get("entry"), decision.get("sl"), decision.get("tp")

    if decision["action"] == "OPEN" and decision["bias"] in ("LONG", "SHORT"):
        if not entry:
            entry = primary["close"]
        # fallback SL/TP dari ATR bila LLM tidak menyediakan (anti posisi tanpa SL)
        if not sl or not tp:
            atr = primary["atr_pct"] / 100.0 * primary["close"]
            sl_dist = max(tcfg.get("atr_sl_mult", 1.5) * atr,
                          max(account.spread, 1e-9) * 2)
            long = decision["bias"] == "LONG"
            if not sl:
                sl = entry - sl_dist if long else entry + sl_dist
            if not tp:
                dist = abs(entry - sl)
                tp = entry + dist * tcfg.get("rr_target", 1.5) if long \
                    else entry - dist * tcfg.get("rr_target", 1.5)
        lot = compute_lot(account.equity, abs(entry - sl),
                          max(account.tick_size, 1e-9), max(account.tick_value, 1e-9),
                          risk_cfg, atr_pct=primary["atr_pct"],
                          confidence=decision.get("confidence", 0.0))
    out = {
        "engine": engine,
        "action": decision["action"],
        "bias": decision["bias"],
        "entry": round(entry, 5) if entry else None,
        "sl": round(sl, 5) if sl else None,
        "tp": round(tp, 5) if tp else None,
        "lot": lot,
        "lot_fraction": decision.get("lot_fraction", 0.01),
        "confidence": decision.get("confidence", 0.0),
        "reasoning": decision.get("reasoning", ""),
    }
    return out


@app.get("/health")
def health():
    cfg = CFG["llm"]
    return {
        "status": "ok",
        "engine": STATE["engine"],
        "forced": STATE["forced"],
        "llm_ready": cfg.get("ready", False),
        "llm_model": cfg.get("model"),
        "json_mode": cfg.get("json_mode", True),
        "symbols": CFG.get("symbols"),
        "mode": _pick_engine("__health__"),
    }


@app.get("/mode")
def get_mode():
    return {"engine": STATE["engine"], "forced": STATE["forced"]}


@app.post("/mode")
def set_mode(payload: dict):
    engine = payload.get("engine", "auto")
    if engine not in ("auto", "llm", "deterministic"):
        return {"error": "engine must be auto|llm|deterministic"}
    STATE["engine"] = engine
    STATE["forced"] = engine != "auto"
    return {"engine": engine, "forced": STATE["forced"]}


@app.post("/decision")
async def decision(request: Request):
    t0 = time.time()
    req = None
    out = None
    try:
        # parse manual: tidak bergantung Content-Type header (WebRequest MQL5
        # tidak bisa set header request)
        raw = await request.json()
        req = DecisionRequest.model_validate(raw)
        # _run_decision mengandung panggilan LLM blocking -> jalankan di
        # threadpool agar event loop tetap melayani request lain (tanpa ini,
        # request mengantri & timeout saat LLM lambat).
        out = await run_in_threadpool(_run_decision, req)
    except Exception as e:
        log.exception("decision error")
        rid = req.request_id if req else ""
        out = {"error": str(e), "request_id": rid,
               "action": "HOLD", "bias": "FLAT", "engine": "error",
               "reasoning": str(e)[:200]}
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


@app.get("/history")
def history(symbol: str, limit: int = 50):
    return {"symbol": symbol, "decisions": MEM.recent(symbol, limit)}


@app.get("/live_report")
def live_report(symbol: str = "XAUUSD", last: int = 200):
    """Ringkasan A/B live: per engine (aktif vs det), hitungan action, agreement.

    'last' = jumlah baris terbaru yang dianalisis dari SQLite.
    """
    rows = MEM.conn.execute(
        "SELECT ts, engine, action, bias FROM decisions "
        "WHERE symbol=? ORDER BY ts DESC LIMIT ?", (symbol, last)).fetchall()
    by_engine = {}
    for _ts, engine, action, bias in rows:
        s = by_engine.setdefault(engine, {})
        s.setdefault(action, {})
        s[action][bias] = s[action].get(bias, 0) + 1
    agree = total = 0
    paired = MEM.conn.execute(
        "SELECT llm_decision, det_decision FROM decisions "
        "WHERE symbol=? ORDER BY ts DESC LIMIT ?", (symbol, last)).fetchall()
    for llm_j, det_j in paired:
        if not llm_j or not det_j:
            continue
        try:
            llm_d = json.loads(llm_j); det_d = json.loads(det_j)
        except Exception:
            continue
        total += 1
        if llm_d.get("action") == det_d.get("action"):
            agree += 1
    return {
        "symbol": symbol,
        "last": len(rows),
        "by_engine": by_engine,
        "agreement": {
            "same": agree, "total": total,
            "rate": (agree / total) if total else 0.0,
        },
    }


@app.get("/ab_report")
def ab_report(symbol: str = "XAUUSD", since: int = 300, lot: float = 0.1,
              fwd: int = 30):
    """A/B report dengan forward-fill replay vs CSV lokal.

    Mengembalikan per-engine (LLM vs deterministic): total/open/win%/net/PF
    plus agreement dan divergent list. Memerlukan file CSV OHLC di
    `python/data/<SYMBOL>_M5.csv` di-mount di container.
    """
    import csv as _csv_mod
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", f"{symbol}_M5.csv")
    bars = []
    bars_index = {}
    try:
        with open(csv_path, newline="") as f:
            r = _csv_mod.DictReader(f)
            for i, row in enumerate(r):
                bars.append({
                    "time": int(float(row["time"])),
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                })
                bars_index[bars[-1]["time"]] = i
    except FileNotFoundError:
        return {"error": f"CSV not found: {csv_path}"}

    ts, tv = TICKS.get(symbol, (1e-5, 1.0))

    rows = MEM.conn.execute(
        "SELECT ts, engine, llm_decision, det_decision, payload FROM decisions "
        "WHERE symbol=? ORDER BY ts DESC LIMIT ?", (symbol, since)).fetchall()
    if not rows:
        return {"symbol": symbol, "error": "no decisions"}

    def _load(j, payload):
        if not j:
            return None
        try:
            d = json.loads(j)
        except Exception:
            return None
        # fallback bar_time dari snapshot payload (data lama tanpa bar_time)
        if not d.get("bar_time") and payload:
            try:
                p = json.loads(payload)
                d["bar_time"] = int(p.get("server_time") or 0)
            except Exception:
                pass
        return d

    per_engine = {"llm": [], "det": []}
    for _ts, engine, llm_j, det_j, payload_j in rows:
        llm_d = _load(llm_j, payload_j)
        det_d = _load(det_j, payload_j)
        if llm_d:
            per_engine["llm"].append(llm_d)
        if det_d:
            per_engine["det"].append(det_d)

    def _eval(records):
        s = {"total": len(records), "open": 0, "win": 0, "pnl": 0.0,
             "gw": 0.0, "gl": 0.0}
        for rec in records:
            if rec.get("action") != "OPEN" or rec.get("bias") not in ("LONG", "SHORT"):
                continue
            idx = bars_index.get(rec.get("bar_time"))
            if idx is None:
                continue
            entry, sl, tp = rec.get("entry"), rec.get("sl"), rec.get("tp")
            if not (entry and sl and tp):
                continue
            side = 1 if rec["bias"] == "LONG" else -1
            exit_price = None
            for k in range(idx + 1, min(idx + 50, len(bars))):
                hi, lo = bars[k]["high"], bars[k]["low"]
                if side == 1:
                    hs, ht = lo <= sl, hi >= tp
                else:
                    hs, ht = hi >= sl, lo <= tp
                if hs and ht:
                    exit_price = sl; break
                if hs:
                    exit_price = sl; break
                if ht:
                    exit_price = tp; break
            if exit_price is None:
                exit_price = bars[-1]["close"]
            pnl = side * (exit_price - entry) / ts * tv * lot
            s["open"] += 1
            s["pnl"] += pnl
            if pnl > 0:
                s["win"] += 1
                s["gw"] += pnl
            else:
                s["gl"] += -pnl
        s["win_rate"] = s["win"] / s["open"] if s["open"] else 0.0
        s["pf"] = s["gw"] / s["gl"] if s["gl"] > 0 else "inf"
        return s

    results = {eng: _eval(per_engine[eng]) for eng in ("llm", "det")}

    agree = total = 0
    divergent = []
    for _ts, engine, llm_j, det_j, payload_j in rows:
        llm_d = _load(llm_j, payload_j)
        det_d = _load(det_j, payload_j)
        if not llm_d or not det_d:
            continue
        total += 1
        if llm_d.get("action") == det_d.get("action"):
            agree += 1
        else:
            divergent.append({
                "ts": llm_d.get("bar_time") or det_d.get("bar_time") or _ts,
                "llm_action": llm_d.get("action"), "det_action": det_d.get("action"),
                "llm_bias": llm_d.get("bias"), "det_bias": det_d.get("bias"),
            })

    return {
        "symbol": symbol,
        "since": since,
        "bars_in_csv": len(bars),
        "results": results,
        "agreement": {"same": agree, "total": total,
                       "rate": (agree / total) if total else 0.0},
        "divergent_first5": divergent[:5],
    }


def init():
    global CFG, MEM, LLM
    CFG = load_config()
    ensure_data_dir(CFG)
    MEM = Memory(resolve_path(CFG, "memory"))
    for sym in CFG.get("symbols", []):
        _init_bar_csv(sym)
    lcfg = CFG["llm"]
    if lcfg.get("ready"):
        LLM = LLMClient(lcfg)
    else:
        log.warning("LLM tidak dikonfigurasi (isi base_url & LLM_API_KEY). "
                    "Pakai MockLLM untuk tes.")
        LLM = MockLLM(lcfg)
    log.info("Bridge siap. engine=%s llm_ready=%s model=%s",
             STATE["engine"], lcfg.get("ready"), lcfg.get("model"))


init()


def main():
    cfg = CFG["server"]
    host = cfg["host"] or "0.0.0.0"
    uvicorn.run(app, host=host, port=cfg["port"], log_level="info")


if __name__ == "__main__":
    main()
