#!/usr/bin/env bash
# Pantau request masuk ke bridge. Tail log dan filter sinyal EA.

set -euo pipefail
cd "$(dirname "$0")/../.."

docker compose logs -f --tail=200 bridge | grep --line-buffered -E "decision|LLM|llm-ready|HTTP Request|ERROR" || true