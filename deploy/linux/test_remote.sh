#!/usr/bin/env bash
# Uji end-to-end bridge remote dari VPS.
# Jalankan: bash deploy/linux/test_remote.sh

set -euo pipefail
cd "$(dirname "$0")/../.."

HOST="${BRIDGE_HOST:-127.0.0.1}"
PORT="${BRIDGE_PORT:-8080}"
TOKEN="${BRIDGE_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "BRIDGE_TOKEN kosong. Set di .env atau env." >&2
  exit 1
fi

PAYLOAD='{"symbol":"XAUUSD","timeframe":"M5","bars":{"M5":{"t":[0],"o":[0],"h":[0],"l":[0],"c":[0],"v":[0]}},"positions":[],"account":{"balance":10000,"equity":10000,"tick_size":0.01,"tick_value":1.0,"spread":0.3},"server_time":1700000000,"log":false,"bridge_token":"'"$TOKEN"'"}'

echo "== /health"
curl -fsS "http://$HOST:$PORT/health"
echo
echo
echo "== /decision (with token)"
curl -fsS -X POST "http://$HOST:$PORT/decision" -H "Content-Type: application/json" -d "$PAYLOAD"
echo
echo
echo "== /decision (no token, expect auth_error)"
RESP=$(curl -s -X POST "http://$HOST:$PORT/decision" -H "Content-Type: application/json" -d '{"symbol":"XAUUSD","bars":{}}')
echo "$RESP"
echo "$RESP" | grep -q '"engine":"auth_error"' && echo "OK: tanpa token ditolak" || echo "FAIL: tanpa token tidak ditolak"