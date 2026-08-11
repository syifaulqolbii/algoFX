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

echo "=== 3. Bridge log (tail 30, sinyal LLM & error) ==="
docker compose logs --tail=30 bridge | grep -E "LLM|httpx|decision|ERROR|bridge" || echo "(tidak ada)"
echo

echo "=== 4. Request count via SQLite (5 menit terakhir) ==="
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
"
echo

echo "=== 5. Token test (auth) ==="
TOKEN="${BRIDGE_TOKEN:-$(grep '^BRIDGE_TOKEN=' python/.env 2>/dev/null | cut -d= -f2)}"
if [ -z "$TOKEN" ]; then
  echo "BRIDGE_TOKEN tidak ditemukan di .env"
  exit 1
fi
PAYLOAD='{"symbol":"XAUUSD","timeframe":"M5","bars":{"M5":{"t":[0],"o":[0],"h":[0],"l":[0],"c":[0],"v":[0]}},"positions":[],"account":{"balance":10000,"equity":10000,"tick_size":0.01,"tick_value":1.0,"spread":0.3},"server_time":1700000000,"log":false,"bridge_token":"'"$TOKEN"'"}'
RESP=$(curl -s -X POST "http://127.0.0.1:8080/decision" -H "Content-Type: application/json" -d "$PAYLOAD")
echo "$RESP" | python -m json.tool | head -8
echo "$RESP" | grep -q '"engine":"llm"' && echo "OK: token valid, LLM aktif" || echo "GAGAL: periksa token"