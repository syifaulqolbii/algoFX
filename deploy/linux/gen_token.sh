#!/usr/bin/env bash
# Generate token acak untuk BRIDGE_TOKEN.

set -euo pipefail
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
elif command -v openssl >/dev/null 2>&1; then
  openssl rand -base64 32
else
  echo "python3 atau openssl dibutuhkan" >&2
  exit 1
fi