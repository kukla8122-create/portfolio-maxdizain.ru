#!/usr/bin/env bash
set -euo pipefail
umask 077

# Explicit MAX production cutover for the Yandex Serverless deployment.
# This is intentionally NOT called by yandex-bootstrap.sh.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
INGRESS_NAME="maximum-maxbot-ingress"
MAX_SECRET_NAME="maximum-maxbot-max"
MAX_API="https://platform-api2.max.ru"

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }

for x in yc curl python3; do command -v "$x" >/dev/null || die "Missing tool: $x"; done
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

say "Resolve Yandex ingress and Lockbox credentials"
IJ="$(yc serverless container get "$INGRESS_NAME" --format json)" || die "Ingress container not found"
IURL="$(printf %s "$IJ" | jget url)"
[ -n "$IURL" ] || die "Ingress invocation URL is missing"
WEBHOOK_URL="${IURL%/}/webhook"

SJ="$(yc lockbox secret get "$MAX_SECRET_NAME" --format json)" || die "MAX Lockbox secret not found"
SID="$(printf %s "$SJ" | jget id)"
[ -n "$SID" ] || die "MAX Lockbox secret id is missing"
MAX_TOKEN="$(yc lockbox payload get --id "$SID" --key max_bot_token)"
WEBHOOK_SECRET="$(yc lockbox payload get --id "$SID" --key max_webhook_secret)"
[ -n "$MAX_TOKEN" ] && [ -n "$WEBHOOK_SECRET" ] || die "MAX credentials are incomplete"

say "Read-only readiness checks"
curl -fsS "${IURL%/}/health" >/dev/null || die "Ingress /health failed"
READY="$(curl -fsS "${IURL%/}/ready")" || die "Ingress /ready failed"
printf %s "$READY" | python3 -c 'import json,sys
d=json.load(sys.stdin)
assert d.get("ok") is True, d
assert d.get("read_only") is True, d
assert d.get("max_api") is True, d
assert d.get("queue") is True, d
assert d.get("public_url_configured") is True, d
' || die "Ingress is not ready for cutover"

ME="$(curl -fsS "$MAX_API/me" -H "Authorization: $MAX_TOKEN")" || die "MAX /me failed"
printf %s "$ME" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("is_bot") is True, d' \
  || die "MAX token does not identify a bot"

SUBSCRIPTIONS="$(curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot read current MAX subscriptions"
SUBSCRIPTIONS="$SUBSCRIPTIONS" TARGET="$WEBHOOK_URL" python3 - <<'PY' || exit $?
import json, os
subs = json.loads(os.environ['SUBSCRIPTIONS']).get('subscriptions') or []
target = os.environ['TARGET']
urls = [x.get('url') for x in subs if isinstance(x, dict) and x.get('url')]
others = [u for u in urls if u != target]
print('Current MAX webhook URLs:', urls or 'none')
if others:
    print('STOP: unrelated MAX webhook subscription exists:', others)
    raise SystemExit(31)
PY

printf '\nTARGET WEBHOOK: %s\n' "$WEBHOOK_URL"
if [ "${CONFIRM_MAX_WEBHOOK_CUTOVER:-}" != "YES" ]; then
  printf 'Type ACTIVATE to switch MAX delivery to this Yandex webhook: '
  read -r answer
  [ "$answer" = "ACTIVATE" ] || die "Cutover cancelled; MAX was not changed"
fi

say "Create or refresh the exact MAX webhook subscription"
PAYLOAD="$(WEBHOOK_URL="$WEBHOOK_URL" WEBHOOK_SECRET="$WEBHOOK_SECRET" python3 - <<'PY'
import json, os
print(json.dumps({
    'url': os.environ['WEBHOOK_URL'],
    'update_types': [
        'bot_added',
        'bot_removed',
        'bot_started',
        'message_created',
        'message_callback',
    ],
    'secret': os.environ['WEBHOOK_SECRET'],
}, ensure_ascii=False))
PY
)"
RESPONSE="$(curl -fsS -X POST "$MAX_API/subscriptions" \
  -H "Authorization: $MAX_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "$PAYLOAD")" || die "MAX subscription POST failed"
printf '%s\n' "$RESPONSE"
printf %s "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("success") is not False, d' \
  || die "MAX rejected the subscription"

VERIFY="$(curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot verify MAX subscriptions"
VERIFY="$VERIFY" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json, os
subs = json.loads(os.environ['VERIFY']).get('subscriptions') or []
target = os.environ['TARGET']
assert any(isinstance(x, dict) and x.get('url') == target for x in subs), subs
print('MAX_WEBHOOK_ACTIVE')
print(target)
PY
