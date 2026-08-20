#!/usr/bin/env bash
set -euo pipefail

# Read-only deployment status probe for «МАКСимум мебель» MAX bot.
# This script MUST NOT create, update, delete, pause, resume, deploy, invoke,
# activate or roll back any Yandex Cloud or MAX resource. It uses no MAX token.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
CLOUD_NAME="maximum-maxbot"
DB_NAME="maximum-maxbot-db"
INGRESS_FN="maximum-maxbot-ingress-fn"
WORKER_FN="maximum-maxbot-worker-fn"
TRIGGER_NAME="maximum-maxbot-function-trigger"

jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }

have(){ command -v "$1" >/dev/null 2>&1; }
for tool in yc python3 curl; do have "$tool" || { printf 'STATUS_ERROR missing_tool=%s\n' "$tool"; exit 2; }; done

printf '=== MAX BOT / READ-ONLY YANDEX STATUS ===\n'
printf 'No MAX token is requested. No cloud mutation is performed.\n'

CJ="$(yc resource-manager cloud get "$CLOUD_ID" --format json 2>/dev/null || true)"
FJ="$(yc resource-manager folder get "$FOLDER_ID" --format json 2>/dev/null || true)"
CLOUD_OK=0
FOLDER_OK=0
if [ -n "$CJ" ] && [ "$(printf %s "$CJ" | jget id)" = "$CLOUD_ID" ] && [ "$(printf %s "$CJ" | jget name)" = "$CLOUD_NAME" ]; then CLOUD_OK=1; fi
if [ -n "$FJ" ] && [ "$(printf %s "$FJ" | jget id)" = "$FOLDER_ID" ] && [ "$(printf %s "$FJ" | jget cloud_id)" = "$CLOUD_ID" ] && [ "$(printf %s "$FJ" | jget status)" = ACTIVE ]; then FOLDER_OK=1; fi
printf 'cloud_identity=%s\n' "$CLOUD_OK"
printf 'folder_active=%s\n' "$FOLDER_OK"

check_function(){
  local name="$1" label="$2" j status url
  j="$(yc serverless function get "$name" --format json 2>/dev/null || true)"
  if [ -z "$j" ]; then
    printf '%s_exists=0\n' "$label"
    printf '%s_active=0\n' "$label"
    [ "$label" = ingress ] && printf 'ingress_public_url=\n'
    return
  fi
  status="$(printf %s "$j" | jget status)"
  printf '%s_exists=1\n' "$label"
  [ "$status" = ACTIVE ] && printf '%s_active=1\n' "$label" || printf '%s_active=0\n' "$label"
  if [ "$label" = ingress ]; then
    url="$(printf %s "$j" | jget http_invoke_url)"
    printf 'ingress_public_url=%s\n' "$url"
    if [ -n "$url" ]; then
      health="$(curl -fsS --max-time 15 "$url" 2>/dev/null || true)"
      if HEALTH="$health" python3 - <<'PY' >/dev/null 2>&1
import json, os
try:
    d=json.loads(os.environ.get('HEALTH',''))
except Exception:
    raise SystemExit(1)
assert d.get('ok') is True
assert d.get('read_only') is True
assert d.get('transport') == 'data-streams'
assert d.get('max_token_present') is False
PY
      then printf 'ingress_health_read_only=1\n'; else printf 'ingress_health_read_only=0\n'; fi
    else
      printf 'ingress_health_read_only=0\n'
    fi
  fi
}

check_function "$INGRESS_FN" ingress
check_function "$WORKER_FN" worker

TJ="$(yc serverless trigger get "$TRIGGER_NAME" --format json 2>/dev/null || true)"
if [ -n "$TJ" ]; then
  printf 'trigger_exists=1\n'
  [ "$(printf %s "$TJ" | jget status)" = ACTIVE ] && printf 'trigger_active=1\n' || printf 'trigger_active=0\n'
else
  printf 'trigger_exists=0\ntrigger_active=0\n'
fi

YJ="$(yc ydb database get "$DB_NAME" --format json 2>/dev/null || true)"
if [ -n "$YJ" ]; then
  printf 'ydb_exists=1\n'
  [ "$(printf %s "$YJ" | jget status)" = RUNNING ] && printf 'ydb_running=1\n' || printf 'ydb_running=0\n'
else
  printf 'ydb_exists=0\nydb_running=0\n'
fi

# Optional read-only stream check when the official YDB CLI is already present.
if have ydb && [ -n "$YJ" ]; then
  YDB_CS="$(printf %s "$YJ" | jget endpoint)"
  YDB_CS="$YDB_CS" python3 - <<'PY' >/tmp/maximum-maxbot-status-ydb 2>/dev/null || true
import os
from urllib.parse import urlsplit, parse_qs
u=urlsplit(os.environ.get('YDB_CS',''))
print(f'{u.scheme}://{u.netloc}')
print((parse_qs(u.query).get('database') or [''])[0])
PY
  YDB_GRPC="$(sed -n '1p' /tmp/maximum-maxbot-status-ydb 2>/dev/null || true)"
  YDB_PATH="$(sed -n '2p' /tmp/maximum-maxbot-status-ydb 2>/dev/null || true)"
  rm -f /tmp/maximum-maxbot-status-ydb
  if [ -n "$YDB_GRPC" ] && [ -n "$YDB_PATH" ]; then
    IAM_TOKEN="$(yc iam create-token 2>/dev/null || true)"; export IAM_TOKEN
    if ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" scheme describe maximum-maxbot-events >/tmp/maximum-maxbot-status-topic 2>/dev/null; then
      printf 'stream_exists=1\n'
      PARTITIONS="$(python3 - /tmp/maximum-maxbot-status-topic <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8',errors='replace').read(); m=re.search(r'PartitionsCount:\s*(\d+)',s); print(m.group(1) if m else '')
PY
)"
      [ "$PARTITIONS" = 1 ] && printf 'stream_one_partition=1\n' || printf 'stream_one_partition=0\n'
    else
      printf 'stream_exists=0\nstream_one_partition=0\n'
    fi
    rm -f /tmp/maximum-maxbot-status-topic
    unset IAM_TOKEN
  else
    printf 'stream_exists=unknown\nstream_one_partition=unknown\n'
  fi
else
  printf 'stream_exists=unknown\nstream_one_partition=unknown\n'
fi

# Final summary uses only the core resources that yc can inspect without secrets.
CORE_READY=1
for pair in \
  "$CLOUD_OK" "$FOLDER_OK"; do [ "$pair" = 1 ] || CORE_READY=0; done
ING="$(yc serverless function get "$INGRESS_FN" --format json 2>/dev/null || true)"
WRK="$(yc serverless function get "$WORKER_FN" --format json 2>/dev/null || true)"
[ -n "$ING" ] && [ "$(printf %s "$ING" | jget status)" = ACTIVE ] || CORE_READY=0
[ -n "$WRK" ] && [ "$(printf %s "$WRK" | jget status)" = ACTIVE ] || CORE_READY=0
[ -n "$TJ" ] && [ "$(printf %s "$TJ" | jget status)" = ACTIVE ] || CORE_READY=0
[ -n "$YJ" ] && [ "$(printf %s "$YJ" | jget status)" = RUNNING ] || CORE_READY=0

if [ "$CORE_READY" = 1 ]; then
  printf 'READ_ONLY_CORE_STATUS=READY\n'
else
  printf 'READ_ONLY_CORE_STATUS=INCOMPLETE\n'
fi
