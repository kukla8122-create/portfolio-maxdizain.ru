#!/usr/bin/env bash
set -euo pipefail
umask 077

# Explicit rollback for the Yandex MAX webhook. Deletes only the exact webhook URL
# owned by maximum-maxbot-ingress and refuses to touch unrelated subscriptions.

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

IJ="$(yc serverless container get "$INGRESS_NAME" --format json)" || die "Ingress container not found"
IURL="$(printf %s "$IJ" | jget url)"
[ -n "$IURL" ] || die "Ingress invocation URL is missing"
WEBHOOK_URL="${IURL%/}/webhook"

SJ="$(yc lockbox secret get "$MAX_SECRET_NAME" --format json)" || die "MAX Lockbox secret not found"
SID="$(printf %s "$SJ" | jget id)"
MAX_TOKEN="$(yc lockbox payload get --id "$SID" --key max_bot_token)"
[ -n "$MAX_TOKEN" ] || die "MAX token is missing"

say "Inspect exact MAX subscription before rollback"
SUBSCRIPTIONS="$(curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot read current MAX subscriptions"
STATE="$(SUBSCRIPTIONS="$SUBSCRIPTIONS" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json, os
subs = json.loads(os.environ['SUBSCRIPTIONS']).get('subscriptions') or []
target = os.environ['TARGET']
urls = [x.get('url') for x in subs if isinstance(x, dict) and x.get('url')]
others = [u for u in urls if u != target]
if others:
    print('OTHER')
elif target in urls:
    print('TARGET')
else:
    print('NONE')
PY
)"

case "$STATE" in
  OTHER) die "Unrelated MAX webhook exists; rollback refuses to modify it" ;;
  NONE) printf 'MAX_WEBHOOK_ALREADY_ABSENT\n%s\n' "$WEBHOOK_URL"; exit 0 ;;
  TARGET) ;;
  *) die "Cannot determine MAX subscription state" ;;
esac

printf '\nTARGET WEBHOOK: %s\n' "$WEBHOOK_URL"
if [ "${CONFIRM_MAX_WEBHOOK_ROLLBACK:-}" != "YES" ]; then
  printf 'Type ROLLBACK to delete this exact MAX webhook subscription: '
  read -r answer
  [ "$answer" = "ROLLBACK" ] || die "Rollback cancelled; MAX was not changed"
fi

say "Delete only the exact Yandex webhook subscription"
curl -fsS -G -X DELETE "$MAX_API/subscriptions" \
  -H "Authorization: $MAX_TOKEN" \
  --data-urlencode "url=$WEBHOOK_URL" >/dev/null \
  || die "MAX subscription DELETE failed"

VERIFY="$(curl -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot verify MAX subscriptions"
VERIFY="$VERIFY" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json, os
subs = json.loads(os.environ['VERIFY']).get('subscriptions') or []
target = os.environ['TARGET']
assert not any(isinstance(x, dict) and x.get('url') == target for x in subs), subs
print('MAX_WEBHOOK_ROLLED_BACK')
print(target)
PY
