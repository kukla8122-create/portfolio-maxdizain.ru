#!/bin/bash
set -euo pipefail

WEBHOOK_URL="https://bot.portfolio-maxdizain.ru/webhook"
HEALTH_URL="https://bot.portfolio-maxdizain.ru/health"
MAX_API="https://platform-api2.max.ru"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root"
  exit 1
fi

for secret_file in /root/.max_token /root/.max_webhook_secret; do
  if [ ! -s "$secret_file" ]; then
    echo "Missing required credential: $secret_file"
    exit 2
  fi
done

MAX_TOKEN="$(cat /root/.max_token)"
WEBHOOK_SECRET="$(cat /root/.max_webhook_secret)"

# Do not subscribe until public HTTPS is really working.
curl -fsS "$HEALTH_URL" >/dev/null

echo "Public HTTPS health check: OK"

PAYLOAD="$(python3 - "$WEBHOOK_URL" "$WEBHOOK_SECRET" <<'PY'
import json, sys
print(json.dumps({
    "url": sys.argv[1],
    "update_types": ["message_created", "bot_started"],
    "secret": sys.argv[2],
}, ensure_ascii=False))
PY
)"

RESPONSE="$(curl -fsS -X POST "$MAX_API/subscriptions" \
  -H "Authorization: $MAX_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "$PAYLOAD")"

echo "MAX subscription response: $RESPONSE"

echo "Current MAX subscriptions:"
curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN"
echo
