#!/usr/bin/env bash
set -euo pipefail
umask 077

# Explicit MAX webhook cutover after the Cloud Functions infrastructure has passed
# its end-to-end synthetic test. This is intentionally separate from bootstrap.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
INGRESS_FN="maximum-maxbot-ingress-fn"
TRIGGER_NAME="maximum-maxbot-function-trigger"
DB_NAME="maximum-maxbot-db"
STREAM_NAME="maximum-maxbot-events"
MAX_API="https://platform-api2.max.ru"
MAX_CHANNEL_LINK="channel_maxmebel_52"

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }

for tool in yc curl python3; do command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"; done
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; unset MAX_TOKEN WEBHOOK_SECRET IAM_TOKEN || true' EXIT
ROOT_CA="$TMP/root.pem"
SUB_CA="$TMP/sub.pem"
MAX_CA="$TMP/max-ca.pem"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o "$ROOT_CA"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt -o "$SUB_CA"
cat /etc/ssl/certs/ca-certificates.crt "$ROOT_CA" "$SUB_CA" > "$MAX_CA"

say "Resolve the exact public Cloud Function URL"
FJ="$(yc serverless function get "$INGRESS_FN" --format json)" || die "Ingress function not found"
[ "$(printf %s "$FJ" | jget status)" = ACTIVE ] || die "Ingress function is not ACTIVE"
WEBHOOK_URL="$(printf %s "$FJ" | jget http_invoke_url)"
[ -n "$WEBHOOK_URL" ] || die "Ingress http_invoke_url is missing"

say "Read-only Yandex readiness checks"
TJ="$(yc serverless trigger get "$TRIGGER_NAME" --format json)" || die "Data Streams trigger not found"
[ "$(printf %s "$TJ" | jget status)" = ACTIVE ] || die "Data Streams trigger is not ACTIVE"
YJ="$(yc ydb database get "$DB_NAME" --format json)" || die "YDB database not found"
[ "$(printf %s "$YJ" | jget status)" = RUNNING ] || die "YDB database is not RUNNING"
YDB_CS="$(printf %s "$YJ" | jget endpoint)"
YDB_CS="$YDB_CS" python3 - <<'PY' >"$TMP/ydb"
import os
from urllib.parse import urlsplit,parse_qs
u=urlsplit(os.environ['YDB_CS'])
print(f'{u.scheme}://{u.netloc}')
print((parse_qs(u.query).get('database') or [''])[0])
PY
YDB_GRPC="$(sed -n '1p' "$TMP/ydb")"
YDB_PATH="$(sed -n '2p' "$TMP/ydb")"
if command -v ydb >/dev/null 2>&1; then
  IAM_TOKEN="$(yc iam create-token)"; export IAM_TOKEN
  ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" scheme describe "$STREAM_NAME" >"$TMP/topic"
  PARTITIONS="$(python3 - "$TMP/topic" <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8',errors='replace').read();m=re.search(r'PartitionsCount:\s*(\d+)',s);print(m.group(1) if m else '')
PY
)"
  unset IAM_TOKEN
  [ "$PARTITIONS" = 1 ] || die "Data Stream must have exactly one partition"
fi
HEALTH="$(curl -fsS "$WEBHOOK_URL")" || die "Ingress HTTPS health failed"
printf %s "$HEALTH" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d.get("ok") is True and d.get("read_only") is True and d.get("transport")=="data-streams" and d.get("max_token_present") is False,d' \
  || die "Ingress health contract failed"

say "Read and validate MAX token"
printf 'Paste MAX_BOT_TOKEN here (input is hidden): '
read -r -s MAX_TOKEN
printf '\n'
[ -n "$MAX_TOKEN" ] || die "Empty MAX token"
WEBHOOK_SECRET="$(MAX_TOKEN="$MAX_TOKEN" python3 - <<'PY'
import hashlib,os
print(hashlib.sha256(("maximum-webhook-v3:"+os.environ['MAX_TOKEN']).encode()).hexdigest())
PY
)"
ME="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/me" -H "Authorization: $MAX_TOKEN")" || die "MAX /me failed"
printf %s "$ME" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d.get("is_bot") is True,d' || die "MAX token is invalid"

say "Verify MAX channel identity and bot permissions before cutover"
CHANNEL="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/chats/$MAX_CHANNEL_LINK" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot resolve configured MAX channel"
CHANNEL_ID="$(printf %s "$CHANNEL" | jget chat_id)"
[ "$(printf %s "$CHANNEL" | jget type)" = channel ] || die "Configured link is not a channel"
[ "$(printf %s "$CHANNEL" | jget status)" = active ] || die "Bot/channel relation is not active"
[ -n "$CHANNEL_ID" ] || die "Channel chat_id missing"
MEMBERSHIP="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/chats/$CHANNEL_ID/members/me" -H "Authorization: $MAX_TOKEN")" \
  || die "Cannot read bot membership in MAX channel"
MEMBERSHIP="$MEMBERSHIP" python3 - <<'PY'
import json,os
m=json.loads(os.environ['MEMBERSHIP']);p=set(m.get('permissions') or []);need={'read_all_messages','write'};missing=sorted(need-p)
print('MAX channel permissions:',sorted(p))
if missing: raise SystemExit('Missing MAX channel permissions: '+','.join(missing))
PY

say "Inspect current MAX webhook subscriptions without changing them"
SUBS="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" || die "Cannot read MAX subscriptions"
SUBS="$SUBS" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json,os
subs=json.loads(os.environ['SUBS']).get('subscriptions') or [];target=os.environ['TARGET']
urls=[x.get('url') for x in subs if isinstance(x,dict) and x.get('url')]
others=[u for u in urls if u!=target]
print('Current MAX webhook URLs:',urls or 'none')
if others:
    print('STOP: unrelated MAX webhook subscription exists:',others)
    raise SystemExit(31)
PY

printf '\nTARGET WEBHOOK: %s\n' "$WEBHOOK_URL"
if [ "${CONFIRM_MAX_WEBHOOK_CUTOVER:-}" != "YES" ]; then
  printf 'Type ACTIVATE to switch MAX delivery to this Cloud Function: '
  read -r answer
  [ "$answer" = ACTIVATE ] || die "Cutover cancelled; MAX was not changed"
fi

say "Create/refresh exact production MAX webhook subscription"
PAYLOAD="$(TARGET="$WEBHOOK_URL" SECRET="$WEBHOOK_SECRET" python3 - <<'PY'
import json,os
print(json.dumps({'url':os.environ['TARGET'],'update_types':['bot_added','bot_removed','bot_started','message_created','message_callback'],'secret':os.environ['SECRET']},ensure_ascii=False,separators=(',',':')))
PY
)"
RESPONSE="$(curl --cacert "$MAX_CA" -fsS -X POST "$MAX_API/subscriptions" \
  -H "Authorization: $MAX_TOKEN" -H "Content-Type: application/json" --data-binary "$PAYLOAD")" \
  || die "MAX subscription POST failed"
printf '%s\n' "$RESPONSE"
printf %s "$RESPONSE" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d.get("success") is not False,d' || die "MAX rejected subscription"
VERIFY="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" || die "Cannot verify subscriptions"
VERIFY="$VERIFY" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json,os
subs=json.loads(os.environ['VERIFY']).get('subscriptions') or [];target=os.environ['TARGET']
assert any(isinstance(x,dict) and x.get('url')==target for x in subs),subs
print('MAX_WEBHOOK_ACTIVE')
print(target)
PY
