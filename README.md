# Regime EA — MT5 + DeepSeek (OpenAI-compatible)

EA hybrid yang **fully dinamis mengikuti regime market**: mesin deterministik
(multi-TF feature → klasifikasi regime) selalu jalan, dan di live keputusan
final diambil LLM (DeepSeek via custom OpenAI-compatible provider). Jika
bridge/LLM mati, EA otomatis fallback ke deterministik — dan itu juga yang
dipakai di strategy tester (backtest baseline, tanpa network).

```
MT5 Terminal (RegimeEA.mq5)
  |  WebRequest POST http://127.0.0.1:8080/decision  (per close bar M5)
  v
Python Bridge (localhost, FastAPI)
  features.py -> regime.py -> llm.py (DeepSeek) / mock.py / deterministic fallback
                -> memory.py (SQLite) -> sinyal JSON -> EA eksekusi
```

## Struktur

```
forex_algo/
├─ mql5/
│  ├─ RegimeEA.mq5            EA utama
│  └─ include/
│     ├─ Json.mqh             parser JSON minimal
│     ├─ Features.mqh         fitur multi-TF (parity Python)
│     ├─ Deterministic.mqh    regime + sinyal fallback (parity Python)
│     └─ Bridge.mqh           WebRequest client
├─ python/
│  ├─ config.yaml             semua pengaturan (simbol, risk, LLM, regime)
│  ├─ config.py               loader
│  ├─ features.py             fitur: ATR, ADX, ER, RSI, EMA, volatilitas
│  ├─ regime.py               klasifikasi regime + sinyal deterministik
│  ├─ llm.py                  client OpenAI-compatible + validasi JSON ketat
│  ├─ mock.py                 mock LLM utk dry-run
│  ├─ memory.py               SQLite log keputusan
│  ├─ server.py               FastAPI bridge
│  └─ backtest/               engine offline (replay data, tanpa LLM)
├─ prompts/decision.md        system prompt untuk DeepSeek
└─ README.md
```

## Setup

1. **Python** (3.10+):
   ```
   cd python
   pip install -r requirements.txt
   copy .env.example .env      # lalu isi credential di .env
   ```

2. **Credential** (`python/.env`) — semua kredensial di sini, tidak di repo:
   ```
   LLM_API_KEY=sk-...                     # API key provider OpenAI-compatible
   LLM_BASE_URL=https://<provider-router>/v1   # wajib akhiran /v1
   LLM_MODEL=ocGO/deepseek-v4-flash       # model ID harus persis sesuai daftar router
   LLM_MAX_TOKENS=4096                    # reasoning model butuh budget besar
   ```
   - Cek daftar model valid: `GET <base_url>/models` dengan API key.
   - Jika `content` respons kosong, naikkan `LLM_MAX_TOKENS` (token habis dipakai reasoning).

3. **MT5** — whitelist URL bridge:
   `Tools → Options → Expert Advisors → Allow WebRequest → tambahkan http://127.0.0.1:8080`
   Copy `mql5/` ke `MQL5/Experts/RegimeEA/` lalu compile di MetaEditor.

## Menjalankan

```bash
# 1) bridge
cd python
python server.py              # atau: uvicorn server:app --port 8080

# 2) verifikasi
Invoke-RestMethod http://127.0.0.1:8080/health   # llm_ready: true

# 3) pasang EA di chart M5 (demo), InpEnableLLM=true
```

Tanpa API key, bridge otomatis pakai **MockLLM** untuk tes alur.

## Backtest

LLM tidak bisa di-backtest (non-deterministik). Dua lapisan:

- **Baseline (bisa backtest penuh)**: strategi deterministik berbasis regime.
  Matematikanya identik di `python/regime.py` dan `mql5/include/Deterministic.mqh`,
  jadi hasil MT5 strategy tester == offline backtest Python.
  ```
  cd python
  python -m backtest.run --bars 8000 --seed 7           # data sintetis
  python -m backtest.run --csv data/EURUSD_M5.csv       # data asli (time,open,high,low,close,volume)
  ```

- **Lapisan LLM**: validasi di live/demo. Semua keputusan tercatat di SQLite
  (`GET /history?symbol=EURUSD`) sehingga bisa diaudit/divalidasi sebelum
  meningkatkan ukuran posisi.

## Endpoint bridge

| Method | Path | Fungsi |
|---|---|---|
| GET | `/health` | status engine + kesiapan LLM |
| POST | `/decision` | bars OHLCV multi-TF → sinyal (OPEN/CLOSE/MODIFY/HOLD) |
| GET | `/history?symbol=X&limit=N` | log keputusan |
| GET | `/mode` | engine saat ini |
| POST | `/mode` `{"engine":"auto"\|"llm"\|"deterministic"}` | paksa engine |

## Safety

- Keputusan per **close bar** (M5 default), bukan per tick — toleran terhadap
  latency LLM (timeout 15s).
- Output LLM dipaksa **strict JSON** + validasi range; output tidak valid → HOLD.
- **Gate rule**: tidak boleh OPEN saat regime CHOPPY/RANGING, confidence < 0.55 ditolak.
- N kali gagal berturut → EA pindah **permanen** ke engine deterministik.
- Lot di-clamp ke `max_lot`, spread maksimum dicek sebelum entry.

## Strategi deterministik (baseline)

Seluruh knob di `config.yaml` (sinkron dengan input EA):

| Seksi | Knob | Fungsi |
|---|---|---|
| `regime` | `trend_er_min`, `trend_adx_min`, ... | klasifikasi regime |
| `trading` | `min_confidence`, `rr_target`, `atr_sl_mult` | ambang sinyal & SL/TP |
| `entry` | `type: market\|pullback\|breakout` | tipe entry |
| `entry` | `min_htf_agree`, `require_htf_alignment` | gate searah TF besar |
| `entry` | `swing_n`, `breakout_lookback`, `pullback_ema_dist_pct` | parameter entry |
| `session` | `enabled`, `entry_hours`, `quiet_hours` | filter jam (server time) |

Catatan: `pullback` = entry saat pullback ke EMA dalam tren (terbaik di EURUSD);
`breakout` = menembus swing level. `bar_time` dikirim bridge dari `server_time` EA,
di backtest dari `time` bar — parity dijaga di ketiga jalur.
