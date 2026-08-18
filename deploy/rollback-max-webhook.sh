#!/usr/bin/env bash
set -euo pipefail
umask 077

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
INGRESS_FN="maximum-maxbot-ingress-fn"
DB_NAME="maximum-maxbot-db"
MAX_API="https://platform-api2.max.ru"

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys;d=json.load(sys.stdin);cur=d
for p in sys.argv[1].split("."):cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(cur if cur is not None else "")' "$1"; }
for tool in yc curl python3; do command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"; done
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"; unset MAX_TOKEN STORED_MAX_TOKEN IAM_TOKEN || true' EXIT
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o "$TMP/root"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt -o "$TMP/sub"
cat /etc/ssl/certs/ca-certificates.crt "$TMP/root" "$TMP/sub" > "$TMP/ca"

if ! command -v ydb >/dev/null 2>&1; then
  curl -sSL https://install.ydb.tech/cli | bash
  export PATH="$HOME/ydb/bin:$HOME/.local/bin:$PATH"
  if ! command -v ydb >/dev/null 2>&1; then
    YDB_BIN="$(find "$HOME" -type f -name ydb -perm -u+x 2>/dev/null | head -n 1 || true)"
    [ -n "$YDB_BIN" ] || die "YDB CLI unavailable"
    export PATH="$(dirname "$YDB_BIN"):$PATH"
  fi
fi

FJ="$(yc serverless function get "$INGRESS_FN" --format json)" || die "Ingress function not found"
WEBHOOK_URL="$(printf %s "$FJ" | jget http_invoke_url)"
[ -n "$WEBHOOK_URL" ] || die "Function URL missing"

YJ="$(yc ydb database get "$DB_NAME" --format json)" || die "YDB database not found"
YDB_CS="$(printf %s "$YJ" | jget endpoint)"
YDB_CS="$YDB_CS" python3 - <<'PY' >"$TMP/ydb"
import os
from urllib.parse import urlsplit,parse_qs
u=urlsplit(os.environ['YDB_CS']);print(f'{u.scheme}://{u.netloc}');print((parse_qs(u.query).get('database') or [''])[0])
PY
YDB_GRPC="$(sed -n '1p' "$TMP/ydb")"; YDB_PATH="$(sed -n '2p' "$TMP/ydb")"

# Read the deployed bot identity with a parameterized YDB query. Avoid embedded
# string literals so the rollback path uses the same current ydb sql contract as
# bootstrap and cutover.
cat >"$TMP/read-token.sql" <<'SQL'
DECLARE $key AS Utf8;
SELECT value FROM deployment_secrets WHERE key=$key LIMIT 1;
SQL
printf '%s\n' '{"key":"max_bot_token"}' >"$TMP/read-token.json"
IAM_TOKEN="$(yc iam create-token)"; export IAM_TOKEN
ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" sql \
  -f "$TMP/read-token.sql" --input-file "$TMP/read-token.json" \
  --format json-unicode >"$TMP/token.out"
STORED_MAX_TOKEN="$(python3 - "$TMP/token.out" <<'PY'
import json,sys
v=''
for line in open(sys.argv[1],encoding='utf-8',errors='replace'):
    line=line.strip()
    if not line: continue
    try:r=json.loads(line)
    except Exception:continue
    if isinstance(r,dict) and r.get('value'):v=str(r['value']);break
print(v)
PY
)"
unset IAM_TOKEN
[ -n "$STORED_MAX_TOKEN" ] || die "Stored MAX token missing"

printf 'Paste MAX_BOT_TOKEN here (input is hidden): '
read -r -s MAX_TOKEN
printf '\n'
[ -n "$MAX_TOKEN" ] || die "Empty token"
[ "$MAX_TOKEN" = "$STORED_MAX_TOKEN" ] || die "This token does not match the deployed MAX bot"
SUBS="$(curl --cacert "$TMP/ca" -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")" || die "Cannot read MAX subscriptions"
SUBS="$SUBS" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json,os
urls=[x.get('url') for x in (json.loads(os.environ['SUBS']).get('subscriptions') or []) if isinstance(x,dict) and x.get('url')]
t=os.environ['TARGET'];others=[u for u in urls if u!=t]
print('Current MAX webhook URLs:',urls or 'none')
if others: print('Unrelated MAX webhook exists and will NOT be touched:',others)
if t not in urls: raise SystemExit('Target webhook is not currently subscribed; nothing to roll back')
PY
printf '\nTARGET WEBHOOK TO REMOVE: %s\n' "$WEBHOOK_URL"
if [ "${CONFIRM_MAX_WEBHOOK_ROLLBACK:-}" != "YES" ]; then
  printf 'Type ROLLBACK to remove only this exact MAX webhook: '
  read -r answer
  [ "$answer" = ROLLBACK ] || die "Rollback cancelled; MAX was not changed"
fi
say "Delete only the exact Cloud Function webhook URL"
curl --cacert "$TMP/ca" -fsS -G -X DELETE "$MAX_API/subscriptions" \
  -H "Authorization: $MAX_TOKEN" --data-urlencode "url=$WEBHOOK_URL" >/dev/null
VERIFY="$(curl --cacert "$TMP/ca" -fsS "$MAX_API/subscriptions" -H "Authorization: $MAX_TOKEN")"
VERIFY="$VERIFY" TARGET="$WEBHOOK_URL" python3 - <<'PY'
import json,os
subs=json.loads(os.environ['VERIFY']).get('subscriptions') or [];t=os.environ['TARGET']
assert not any(isinstance(x,dict) and x.get('url')==t for x in subs),subs
print('MAX_WEBHOOK_ROLLED_BACK')
PY
