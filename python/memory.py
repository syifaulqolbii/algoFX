"""Pencatatan keputusan & regime ke SQLite untuk audit/backtest-replay."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  symbol TEXT,
  timeframe TEXT,
  engine TEXT,
  regime TEXT,
  regime_conf REAL,
  action TEXT,
  bias TEXT,
  request_id TEXT,
  payload TEXT,
  response TEXT,
  error TEXT,
  llm_decision TEXT,
  det_decision TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_ts ON decisions(symbol, ts);
"""


def _dumps(v):
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v)


def _loads(v):
    if not v:
        return None
    try:
        o = json.loads(v)
        if isinstance(o, str):
            o = json.loads(o)
        return o
    except Exception:
        return None


class Memory:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def log_decision(self, symbol, timeframe, engine, regime, action, bias,
                     request_id, payload=None, response=None, error=None,
                     llm_decision=None, det_decision=None):
        with self.lock:
            return self._log_decision(symbol, timeframe, engine, regime, action, bias,
                                      request_id, payload, response, error,
                                      llm_decision, det_decision)

    def _log_decision(self, symbol, timeframe, engine, regime, action, bias,
                      request_id, payload=None, response=None, error=None,
                      llm_decision=None, det_decision=None):
        # migrasi kolom bila DB lama
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(decisions)").fetchall()]
        for col, _ in (("llm_decision", "TEXT"), ("det_decision", "TEXT")):
            if col not in cols:
                try:
                    self.conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} TEXT")
                except Exception:
                    pass
        self.conn.execute(
            "INSERT INTO decisions(ts,symbol,timeframe,engine,regime,regime_conf,"
            "action,bias,request_id,payload,response,error,llm_decision,det_decision) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), symbol, timeframe, engine, regime.get("label") if regime else None,
             regime.get("confidence") if regime else None,
             action, bias, request_id,
             _dumps(payload), _dumps(response),
             str(error) if error else None,
             _dumps(llm_decision), _dumps(det_decision)),
        )
        self.conn.commit()

    def recent(self, symbol, limit=50):
        rows = self.conn.execute(
            "SELECT ts,engine,regime,regime_conf,action,bias FROM decisions "
            "WHERE symbol=? ORDER BY ts DESC LIMIT ?", (symbol, limit)).fetchall()
        return [{
            "ts": r[0], "engine": r[1], "regime": r[2], "regime_conf": r[3],
            "action": r[4], "bias": r[5],
        } for r in rows]

    def ab_rows(self, symbol, limit=5000):
        """Baris keputusan lengkap utk A/B replay (payload + kedua engine)."""
        rows = self.conn.execute(
            "SELECT ts,symbol,engine,action,payload,llm_decision,det_decision "
            "FROM decisions WHERE symbol=? ORDER BY ts LIMIT ?", (symbol, limit)).fetchall()
        out = []
        for r in rows:
            out.append({
                "ts": r[0], "symbol": r[1], "engine": r[2], "action": r[3],
                "payload": _loads(r[4]),
                "llm": _loads(r[5]),
                "det": _loads(r[6]),
            })
        return out

    def close(self):
        self.conn.close()
