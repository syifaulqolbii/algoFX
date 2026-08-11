#!/usr/bin/env bash
# Verifikasi EA MT5 Windows -> Bridge Linux lewat Tailscale.
# Jalankan di VPS: bash deploy/linux/verify_ea.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

echo "=== 1. Container status ==="
docker compose ps
echo

echo "=== 2. Bridge health (host-side) ==="
curl -fsS "http://127.0.0.1:8080/health" || { echo "bridge DOWN"; exit 1; }
echo
echo

echo "=== 3. Bridge log (tail 40, sinyal LLM & error) ==="
docker compose logs --tail=40 bridge | grep -E "POST /decision|LLM|httpx|ERROR|bridge" || echo "(tidak ada)"
echo

echo "=== 4. Request count via SQLite ==="
docker exec algofx-bridge-1 python -c "
from memory import Memory
from config import load_config, resolve_path
import time
m = Memory(resolve_path(load_config(), 'memory'))
cutoff = time.time() - 300
n = m.conn.execute('SELECT COUNT(*) FROM decisions WHERE ts > ?', (cutoff,)).fetchone()[0]
total = m.conn.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
print(f'last 5 min : {n}')
print(f'since start : {total}')
if total:
    last = m.conn.execute('SELECT ts, symbol, engine, action, bias FROM decisions ORDER BY ts DESC LIMIT 3').fetchall()
    print('last 3:')
    for r in last:
        print(' ', r)
else:
    print('BELUM ADA request EA tersimpan. Cek:')
    print('  - MT5: Allow WebRequest = http://100.66.79.3')
    print('  - MT5: EA InpServerUrl=http://100.66.79.3:8080')
    print('  - MT5: EA InpEnableLLM=true, AutoTrading ON')
    print('  - MT5: tab Experts -> log "WebRequest" error')
"
echo

echo "=== 5. Token test (auth) ==="
TOKEN="$(grep '^BRIDGE_TOKEN=' python/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r\n ' || true)"
if [ -z "$TOKEN" ]; then
  echo "INFO: BRIDGE_TOKEN tidak ditemukan di python/.env. Skip test token."
  echo "      Pastikan BRIDGE_TOKEN di .env sama dengan InpBridgeToken di EA."
  exit 0
fi
echo "token loaded (len=${#TOKEN})"
PAYLOAD='{"symbol":"XAUUSD","timeframe":"M5","bars":{"M5":{"t":[0],"o":[0],"h":[0],"l":[0],"c":[0],"v":[0]}},"positions":[],"account":{"balance":10000,"equity":10000,"tick_size":0.01,"tick_value":1.0,"spread":0.3},"server_time":1700000000,"log":false,"bridge_token":"'"$TOKEN"'"}'
RESP=$(curl -s -X POST "http://127.0.0.1:8080/decision" -H "Content-Type: application/json" -d "$PAYLOAD")
echo "$RESP" | python -m json.tool 2>/dev/null | head -10 || echo "$RESP"
if echo "$RESP" | grep -q '"engine":"auth_error"'; then
  echo "GAGAL: token ditolak. Periksa BRIDGE_TOKEN di .env sama dengan InpBridgeToken di EA."
elif echo "$RESP" | grep -q '"engine":"llm"'; then
  echo "OK: token valid, LLM aktif"
else
  echo "INFO: token valid, response di atas"
fi