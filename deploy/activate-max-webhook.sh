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

# Never switch MAX delivery until public HTTPS is actually healthy.
curl -fsS "$HEALTH_URL" >/dev/null
echo "Public HTTPS health check: OK"

# Inspect existing webhook subscriptions first. Refuse to touch an unrelated URL.
SUBSCRIPTIONS="$(curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")"
CHECK="$(python3 -c '
import json, sys
target = sys.argv[1]
data = json.load(sys.stdin)
urls = [s.get("url") for s in data.get("subscriptions", []) if s.get("url")]
print("SAME" if target in urls else "NONE")
others = [u for u in urls if u != target]
if others:
    print("OTHER:" + "|".join(others))
' "$WEBHOOK_URL" <<<"$SUBSCRIPTIONS")"

if grep -q '^OTHER:' <<<"$CHECK"; then
  echo "STOP: another MAX webhook subscription already exists. Nothing was changed."
  echo "$SUBSCRIPTIONS"
  exit 3
fi

# Refresh an existing subscription to this exact URL so secret/update types are current.
if grep -q '^SAME$' <<<"$CHECK"; then
  curl -fsS -G -X DELETE "$MAX_API/subscriptions" \
    -H "Authorization: $MAX_TOKEN" \
    --data-urlencode "url=$WEBHOOK_URL" >/dev/null
  echo "Previous subscription to the same URL removed safely."
fi

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
