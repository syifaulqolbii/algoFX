# Walk-Forward Validation — Hasil (2026-08-07)

## Metode
- Data M5 dari MT5 demo (VantageMarkets): EURUSD/GBPUSD/USDJPY/AUDUSD/USDCAD/USDCHF/XAUUSD (~80–100k bar masing-masing).
- Tuning grid: entry_type {market, pullback, breakout} × rr {1.5, 3.0} @ regime adx≥34, er≥0.5, conf≥0.6.
- Mode OOS: tune di EURUSD → tes semua simbol (full data).
- Mode rolling 3-fold per simbol (train hanya data sebelum fold tes).

## Hasil OOS (tuned di EURUSD: pullback rr=3.0)

| symbol | bars | trades | win | PF | net | DD |
|---|---|---|---|---|---|---|
| AUDUSD | 100000 | 292 | 0.219 | 0.83 | -1770 | 2155 |
| EURUSD | 100000 | 265 | 0.291 | 1.22 | 1963 | 838 |
| GBPUSD | 97679 | 284 | 0.215 | 0.84 | -1747 | 2870 |
| USDCAD | 100000 | 277 | 0.245 | 1.00 | -39 | 1105 |
| USDCHF | 97687 | 248 | 0.214 | 0.81 | -1688 | 2597 |
| USDJPY | 97679 | 321 | 0.231 | 0.93 | -793 | 1568 |
| XAUUSD | 80000 | 225 | 0.262 | 1.07 | 578 | 1725 |

**Gate OOS: FAIL** — PF≥1.0 hanya 2/7, net positif 2/7, median PF 0.929.

## Hasil rolling (robustness waktu)

- **EURUSD**: 1/2 fold positif, median PF 1.063, median net +234 → MARGINAL.
- **XAUUSD**: 2/2 fold positif, median PF 1.228, median net +1253 → ROBUST (market, rr=1.5).

## Kesimpulan
1. **Gate walk-forward GAGAL untuk universe forex.** Baseline deterministik tidak
   generalisasi lintas simbol — hanya profitable di EURUSD (marginal) dan XAUUSD.
2. **XAUUSD satu-satunya yang robust lintas waktu.** Emas cenderung trending;
   cocok dengan strategi regime-trend-following.
3. Parameter terpilih TIDAK ditulis ke config.yaml (default tetap konservatif).
4. Implikasi arsitektur: layer LLM (Item 4) adalah sumber adaptivitas yang
   sebenarnya — baseline deterministik hanya menjadi lantai aman (fallback),
   bukan sumber profit utama. Universe live disarankan dibatasi (XAUUSD ± EURUSD).
