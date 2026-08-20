#!/usr/bin/env bash
set -uo pipefail
umask 077

# Read-only MAX bot token diagnostic.
# It performs only GET /me against the current official MAX API domain.
# It NEVER creates/updates/deletes subscriptions and NEVER mutates Yandex Cloud.

MAX_API="https://platform-api2.max.ru"
TMP="$(mktemp -d)"
MAX_TOKEN=""
cleanup(){
  unset MAX_TOKEN || true
  rm -rf "$TMP"
}
trap cleanup EXIT

die(){ printf 'MAX_TOKEN_CHECK=LOCAL_ERROR detail=%s\n' "$1" >&2; exit 2; }
for tool in curl python3; do command -v "$tool" >/dev/null 2>&1 || die "missing_$tool"; done
[ -r /dev/tty ] || die "tty_unavailable"

printf '=== MAX BOT / READ-ONLY TOKEN CHECK ===\n'
printf 'Only GET /me is called. Webhook/subscriptions are not changed.\n'

ROOT_CA="$TMP/russian-root.pem"
SUB_CA="$TMP/russian-sub.pem"
MAX_CA="$TMP/max-ca.pem"
if ! curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
  https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o "$ROOT_CA"; then
  printf 'MAX_TOKEN_CHECK=CA_DOWNLOAD_ERROR\n'
  exit 3
fi
if ! curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
  https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt -o "$SUB_CA"; then
  printf 'MAX_TOKEN_CHECK=CA_DOWNLOAD_ERROR\n'
  exit 3
fi
cat /etc/ssl/certs/ca-certificates.crt "$ROOT_CA" "$SUB_CA" > "$MAX_CA"

printf 'Paste NEW MAX_BOT_TOKEN here (input is hidden): '
IFS= read -r -s MAX_TOKEN </dev/tty
printf '\n'
[ -n "$MAX_TOKEN" ] || { printf 'MAX_TOKEN_CHECK=EMPTY\n'; exit 4; }

BODY="$TMP/me.json"
ERR="$TMP/curl.err"
set +e
HTTP_CODE="$(curl --cacert "$MAX_CA" --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  --output "$BODY" --write-out '%{http_code}' \
  -X GET "$MAX_API/me" -H "Authorization: $MAX_TOKEN" 2>"$ERR")"
CURL_RC=$?
set -e

if [ "$CURL_RC" -ne 0 ]; then
  printf 'MAX_TOKEN_CHECK=TRANSPORT_ERROR curl_exit=%s http=%s\n' "$CURL_RC" "${HTTP_CODE:-000}"
  # Curl diagnostics can safely be shown: the Authorization value is never echoed.
  sed -n '1,3p' "$ERR" | sed 's/^/transport_detail=/'
  exit 5
fi

case "$HTTP_CODE" in
  200)
    if python3 - "$BODY" <<'PY'
import json, sys
p=sys.argv[1]
with open(p,encoding='utf-8') as f:
    d=json.load(f)
if d.get('is_bot') is not True:
    raise SystemExit(1)
print('MAX_TOKEN_CHECK=VALID')
print('bot_user_id='+str(d.get('user_id','')))
print('bot_username='+str(d.get('username') or ''))
print('bot_name='+str(d.get('first_name') or d.get('name') or ''))
PY
    then
      exit 0
    else
      printf 'MAX_TOKEN_CHECK=BAD_200_RESPONSE\n'
      exit 6
    fi
    ;;
  401)
    printf 'MAX_TOKEN_CHECK=INVALID_HTTP_401\n'
    exit 7
    ;;
  429)
    printf 'MAX_TOKEN_CHECK=RATE_LIMIT_HTTP_429\n'
    exit 8
    ;;
  500|502|503|504)
    printf 'MAX_TOKEN_CHECK=MAX_SERVER_HTTP_%s\n' "$HTTP_CODE"
    exit 9
    ;;
  *)
    printf 'MAX_TOKEN_CHECK=UNEXPECTED_HTTP_%s\n' "${HTTP_CODE:-000}"
    exit 10
    ;;
esac
