#!/usr/bin/env bash
set -euo pipefail
umask 077

# «МАКСимум мебель» MAX bot — Yandex Cloud Functions bootstrap.
# Production path: MAX -> public Cloud Function -> YDB Topic/Data Streams ->
# Data Streams trigger -> private Cloud Function -> YDB.
#
# This script NEVER creates, replaces, or deletes a MAX webhook subscription.
# Webhook cutover is a separate explicit deploy/activate-max-webhook.sh action.
# It also intentionally avoids Yandex Container Registry and Yandex Lockbox so
# there is no inherent recurring image-storage or secret-version charge.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
CLOUD_NAME="maximum-maxbot"
BRANCH="maxbot-production"
REPO="https://github.com/kukla8122-create/portfolio-maxdizain.ru.git"
MAX_API="https://platform-api2.max.ru"
DB_NAME="maximum-maxbot-db"
STREAM_NAME="maximum-maxbot-events"
DLQ_NAME="maximum-maxbot-dlq"
INGRESS_FN="maximum-maxbot-ingress-fn"
WORKER_FN="maximum-maxbot-worker-fn"
TRIGGER_NAME="maximum-maxbot-function-trigger"
OLD_TRIGGER_1="maximum-maxbot-worker-trigger"
OLD_TRIGGER_2="maximum-maxbot-stream-trigger"

TMP="$(mktemp -d)"
SRC="$TMP/src"
PKG="$TMP/function-src"
VENV="$TMP/venv"
TEMP_ACCESS_KEY_RESOURCE_ID=""
TEMP_YMQ_ADMIN=0
ING_SA=""

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }
getj(){ "$@" --format json 2>/dev/null || true; }

cleanup(){
  if [ -n "${TEMP_ACCESS_KEY_RESOURCE_ID:-}" ]; then
    yc iam access-key delete "$TEMP_ACCESS_KEY_RESOURCE_ID" >/dev/null 2>&1 || true
  fi
  if [ "${TEMP_YMQ_ADMIN:-0}" = 1 ] && [ -n "${ING_SA:-}" ]; then
    yc resource-manager folder remove-access-binding "$FOLDER_ID" \
      --role ymq.admin --service-account-id "$ING_SA" >/dev/null 2>&1 || true
  fi
  unset MAX_BOT_TOKEN MAX_WEBHOOK_SECRET IAM_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY || true
  rm -rf "$TMP"
}
trap cleanup EXIT

for tool in yc git python3 curl; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"
done

yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

say "Verify exact Yandex cloud and folder"
CJ="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FJ="$(yc resource-manager folder get "$FOLDER_ID" --format json)"
[ "$(printf %s "$CJ" | jget id)" = "$CLOUD_ID" ] || die "Wrong cloud id"
[ "$(printf %s "$CJ" | jget name)" = "$CLOUD_NAME" ] || die "Wrong cloud name"
# Current Cloud.Get does not expose a status field; Folder.Get still does.
[ "$(printf %s "$FJ" | jget id)" = "$FOLDER_ID" ] || die "Wrong folder id"
[ "$(printf %s "$FJ" | jget cloud_id)" = "$CLOUD_ID" ] || die "Folder belongs to another cloud"
[ "$(printf %s "$FJ" | jget status)" = ACTIVE ] || die "Folder is not ACTIVE"
printf 'Cloud/folder identity: OK\n'

say "Clone production branch and test code before cloud mutations"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC" >/dev/null 2>&1
cd "$SRC"
if ! python3 -m venv "$VENV" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" -q install --disable-pip-version-check -r requirements-yandex.txt
"$VENV/bin/python" -m py_compile \
  maxbot-selfhosted.py maxbot-selfhosted-yandex.py maxbot_yandex_functions.py
"$VENV/bin/python" -m unittest discover -s tests -v
printf 'Python compilation/tests: OK\n'

say "Prepare MAX Ministry-of-Digital-Development CA trust before token validation"
ROOT_CA="$TMP/russian-root.pem"
SUB_CA="$TMP/russian-sub.pem"
MAX_CA="$TMP/max-ca.pem"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
  https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o "$ROOT_CA"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
  https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt -o "$SUB_CA"
cat /etc/ssl/certs/ca-certificates.crt "$ROOT_CA" "$SUB_CA" > "$MAX_CA"

say "Read and validate MAX bot token without changing subscriptions"
printf 'Paste MAX_BOT_TOKEN here (input is hidden): '
read -r -s MAX_BOT_TOKEN
printf '\n'
[ -n "$MAX_BOT_TOKEN" ] || die "Empty MAX token"
MAX_WEBHOOK_SECRET="$(MAX_BOT_TOKEN="$MAX_BOT_TOKEN" python3 - <<'PY'
import hashlib, os
print(hashlib.sha256(("maximum-webhook-v3:" + os.environ["MAX_BOT_TOKEN"]).encode()).hexdigest())
PY
)"
ME="$(curl --cacert "$MAX_CA" -fsS "$MAX_API/me" -H "Authorization: $MAX_BOT_TOKEN")" \
  || die "MAX /me failed"
printf %s "$ME" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("is_bot") is True, d' \
  || die "MAX token does not identify a bot"
printf 'MAX token: valid; webhook secret: derived and stable\n'

if ! command -v ydb >/dev/null 2>&1; then
  say "Install official YDB CLI"
  curl -sSL https://install.ydb.tech/cli | bash
  export PATH="$HOME/ydb/bin:$HOME/.local/bin:$PATH"
  if ! command -v ydb >/dev/null 2>&1; then
    YDB_BIN="$(find "$HOME" -type f -name ydb -perm -u+x 2>/dev/null | head -n 1 || true)"
    [ -n "$YDB_BIN" ] || die "YDB CLI installation completed but executable was not found"
    export PATH="$(dirname "$YDB_BIN"):$PATH"
  fi
fi
command -v ydb >/dev/null 2>&1 || die "YDB CLI unavailable"

ensure_sa(){
  local name="$1" description="$2" j
  j="$(getj yc iam service-account get "$name")"
  if [ -z "$j" ]; then
    yc iam service-account create "$name" --folder-id "$FOLDER_ID" \
      --description "$description" >/dev/null
    j="$(yc iam service-account get "$name" --format json)"
  fi
  printf %s "$j" | jget id
}
grant_folder(){
  yc resource-manager folder add-access-binding "$FOLDER_ID" \
    --role "$2" --service-account-id "$1" >/dev/null
}
ensure_function(){
  local name="$1" description="$2" j
  j="$(getj yc serverless function get "$name")"
  if [ -z "$j" ]; then
    yc serverless function create --name "$name" --description "$description" \
      --folder-id "$FOLDER_ID" >/dev/null
    j="$(yc serverless function get "$name" --format json)"
  fi
  printf %s "$j"
}

say "Create/reuse dedicated service accounts with least practical runtime roles"
ING_SA="$(ensure_sa maxbot-ingress-sa 'MAX public webhook -> YDB Topic writer')"
WRK_SA="$(ensure_sa maxbot-worker-sa 'MAX private business worker -> YDB')"
TRG_SA="$(ensure_sa maxbot-trigger-sa 'MAX Data Streams trigger')"
grant_folder "$ING_SA" yds.writer
grant_folder "$WRK_SA" ydb.editor
grant_folder "$TRG_SA" yds.admin
grant_folder "$TRG_SA" ymq.writer

say "Create/reuse YDB Serverless database with zero provisioned RCU"
YJ="$(getj yc ydb database get "$DB_NAME")"
if [ -z "$YJ" ]; then
  yc ydb database create "$DB_NAME" --folder-id "$FOLDER_ID" \
    --serverless --sls-provisioned-rcu 0 --deletion-protection >/dev/null
fi
YS=""
for _ in $(seq 1 90); do
  YJ="$(yc ydb database get "$DB_NAME" --format json)"
  YS="$(printf %s "$YJ" | jget status)"
  [ "$YS" = RUNNING ] && break
  [ "$YS" = ERROR ] && die "YDB entered ERROR state"
  sleep 4
done
[ "$YS" = RUNNING ] || die "YDB startup timeout"
YDB_CS="$(printf %s "$YJ" | jget endpoint)"
[ -n "$YDB_CS" ] || die "YDB endpoint missing"
YDB_CS="$YDB_CS" python3 - <<'PY' >"$TMP/ydb-connection"
import os
from urllib.parse import urlsplit, parse_qs
u=urlsplit(os.environ['YDB_CS'])
endpoint=f'{u.scheme}://{u.netloc}'
database=(parse_qs(u.query).get('database') or [''])[0]
if not endpoint.startswith('grpcs://') or not database.startswith('/'):
    raise SystemExit('Cannot parse YDB endpoint')
print(endpoint)
print(database)
PY
YDB_GRPC="$(sed -n '1p' "$TMP/ydb-connection")"
YDB_PATH="$(sed -n '2p' "$TMP/ydb-connection")"
[ -n "$YDB_GRPC" ] && [ -n "$YDB_PATH" ] || die "Cannot parse YDB connection"
printf 'YDB: %s\n' "$YDB_PATH"

say "Create/reuse one-partition Data Stream: 128 KB/s, 1h retention"
IAM_TOKEN="$(yc iam create-token)"
export IAM_TOKEN
if ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" \
  scheme describe "$STREAM_NAME" >"$TMP/topic-before.txt" 2>"$TMP/topic-before.err"; then
  true
else
  ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" topic create \
    --partitions-count 1 \
    --retention-period 1h \
    --partition-write-speed-kbps 128 \
    --metering-mode reserved-capacity \
    --supported-codecs raw \
    "$STREAM_NAME" >/dev/null \
    || { cat "$TMP/topic-before.err" >&2 || true; die "Data Stream creation failed"; }
fi
ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" topic alter \
  --retention-period 1h \
  --partition-write-speed-kbps 128 \
  --metering-mode reserved-capacity \
  --supported-codecs raw \
  "$STREAM_NAME" >/dev/null
ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" scheme describe "$STREAM_NAME" \
  >"$TMP/topic-after.txt"
PARTITIONS="$(python3 - "$TMP/topic-after.txt" <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8',errors='replace').read()
m=re.search(r'PartitionsCount:\s*(\d+)',s)
print(m.group(1) if m else '')
PY
)"
[ "$PARTITIONS" = 1 ] || die "Data Stream must have exactly one partition; found: $PARTITIONS"
unset IAM_TOKEN
printf 'Data Stream ordering guard: 1 partition\n'

say "Create/reuse Standard YMQ DLQ using a temporary access key only"
# Static credentials are needed only by the provisioning client. They are deleted
# immediately and are never saved in a function environment or repository.
grant_folder "$ING_SA" ymq.admin
TEMP_YMQ_ADMIN=1
sleep 5
yc iam access-key create --service-account-id "$ING_SA" \
  --description maximum-maxbot-temporary-dlq-provisioning --format json >"$TMP/key.json"
AWS_ACCESS_KEY_ID="$(cat "$TMP/key.json" | jget access_key.key_id)"
AWS_SECRET_ACCESS_KEY="$(cat "$TMP/key.json" | jget secret)"
TEMP_ACCESS_KEY_RESOURCE_ID="$(cat "$TMP/key.json" | jget access_key.id)"
[ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] && [ -n "$TEMP_ACCESS_KEY_RESOURCE_ID" ] \
  || die "Temporary access key creation returned incomplete data"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
DLQ_NAME="$DLQ_NAME" OUT="$TMP/dlq.json" "$VENV/bin/python" - <<'PY'
import boto3,json,os
s=boto3.client('sqs',endpoint_url='https://message-queue.api.cloud.yandex.net',region_name='ru-central1',
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])
try:
    url=s.get_queue_url(QueueName=os.environ['DLQ_NAME'])['QueueUrl']
except Exception:
    url=s.create_queue(QueueName=os.environ['DLQ_NAME'],Attributes={'MessageRetentionPeriod':'1209600'})['QueueUrl']
s.set_queue_attributes(QueueUrl=url,Attributes={'MessageRetentionPeriod':'1209600'})
attrs=s.get_queue_attributes(QueueUrl=url,AttributeNames=['QueueArn'])['Attributes']
json.dump({'url':url,'arn':attrs['QueueArn']},open(os.environ['OUT'],'w'))
PY
DLQ_ARN="$(cat "$TMP/dlq.json" | jget arn)"
[ -n "$DLQ_ARN" ] || die "DLQ ARN missing"
yc iam access-key delete "$TEMP_ACCESS_KEY_RESOURCE_ID" >/dev/null
TEMP_ACCESS_KEY_RESOURCE_ID=""
yc resource-manager folder remove-access-binding "$FOLDER_ID" \
  --role ymq.admin --service-account-id "$ING_SA" >/dev/null
TEMP_YMQ_ADMIN=0
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
printf 'Temporary YMQ access key: deleted\n'

say "Build small Cloud Functions source package; no Docker/Registry/Lockbox"
mkdir -p "$PKG"
cp maxbot_yandex_functions.py "$PKG/maxbot_yandex_functions.py"
cp maxbot-selfhosted.py "$PKG/maxbot-selfhosted-core.py"
cp maxbot-selfhosted-yandex.py "$PKG/maxbot-selfhosted-yandex.py"
cp requirements-functions.txt "$PKG/requirements.txt"
cat "$ROOT_CA" "$SUB_CA" > "$PKG/max-russian-ca.pem"
python3 -m py_compile "$PKG/maxbot_yandex_functions.py" "$PKG/maxbot-selfhosted-yandex.py" "$PKG/maxbot-selfhosted-core.py"

say "Create/reuse Cloud Functions"
INGJ="$(ensure_function "$INGRESS_FN" 'Public MAX webhook ingress; token-free')"
WRKJ="$(ensure_function "$WORKER_FN" 'Private MAX business worker')"
INGRESS_ID="$(printf %s "$INGJ" | jget id)"
WORKER_ID="$(printf %s "$WRKJ" | jget id)"
[ -n "$INGRESS_ID" ] && [ -n "$WORKER_ID" ] || die "Function IDs missing"

say "Deploy token-free public ingress function"
yc serverless function version create \
  --function-id "$INGRESS_ID" \
  --runtime python312 \
  --entrypoint maxbot_yandex_functions.ingress_handler \
  --memory 256m --execution-timeout 10s \
  --service-account-id "$ING_SA" \
  --environment "YDB_CONNECTION_STRING=$YDB_CS,YDS_TOPIC=$STREAM_NAME,MAX_WEBHOOK_SECRET=$MAX_WEBHOOK_SECRET" \
  --source-path "$PKG" --no-logging >/dev/null
yc serverless function allow-unauthenticated-invoke "$INGRESS_ID" >/dev/null
INGJ="$(yc serverless function get "$INGRESS_ID" --format json)"
IURL="$(printf %s "$INGJ" | jget http_invoke_url)"
[ -n "$IURL" ] || die "Ingress invoke URL missing"

say "Deploy private worker function"
yc serverless function version create \
  --function-id "$WORKER_ID" \
  --runtime python312 \
  --entrypoint maxbot_yandex_functions.worker_handler \
  --memory 256m --execution-timeout 30s \
  --service-account-id "$WRK_SA" \
  --environment "YDB_CONNECTION_STRING=$YDB_CS,MAX_BOT_TOKEN=$MAX_BOT_TOKEN,MAX_WEBHOOK_SECRET=$MAX_WEBHOOK_SECRET" \
  --source-path "$PKG" --no-logging >/dev/null
yc serverless function deny-unauthenticated-invoke "$WORKER_ID" >/dev/null 2>&1 || true
yc serverless function add-access-binding --id "$WORKER_ID" \
  --service-account-id "$TRG_SA" --role functions.functionInvoker >/dev/null

say "Pause exact legacy triggers to prevent duplicate delivery if they exist"
for old in "$OLD_TRIGGER_1" "$OLD_TRIGGER_2"; do
  if yc serverless trigger get "$old" >/dev/null 2>&1; then
    yc serverless trigger pause "$old" >/dev/null 2>&1 || true
    printf 'Paused legacy trigger: %s\n' "$old"
  fi
done

say "Create/update Data Streams trigger -> private worker function + DLQ"
TJ="$(getj yc serverless trigger get "$TRIGGER_NAME")"
if [ -z "$TJ" ]; then
  yc serverless trigger create yds \
    --name "$TRIGGER_NAME" \
    --database "$YDB_PATH" --stream "$STREAM_NAME" \
    --batch-size 1b --batch-cutoff 1s \
    --stream-service-account-id "$TRG_SA" \
    --invoke-function-id "$WORKER_ID" \
    --invoke-function-service-account-id "$TRG_SA" \
    --retry-attempts 5 --retry-interval 10s \
    --dlq-queue-id "$DLQ_ARN" --dlq-service-account-id "$TRG_SA" >/dev/null
else
  yc serverless trigger update yds "$TRIGGER_NAME" \
    --new-database "$YDB_PATH" --new-stream "$STREAM_NAME" \
    --new-stream-service-account-id "$TRG_SA" \
    --new-batch-size 1b --new-batch-cutoff 1s \
    --new-invoke-function-id "$WORKER_ID" \
    --new-invoke-function-service-account-id "$TRG_SA" \
    --new-function-retry-attempts 5 --new-function-retry-interval 10s \
    --new-function-dlq-queue-id "$DLQ_ARN" \
    --new-function-dlq-service-account-id "$TRG_SA" >/dev/null
fi

TRIGGER_STATUS=""
for _ in $(seq 1 60); do
  TJ="$(yc serverless trigger get "$TRIGGER_NAME" --format json)"
  TRIGGER_STATUS="$(printf %s "$TJ" | jget status)"
  [ "$TRIGGER_STATUS" = ACTIVE ] && break
  sleep 2
done
[ "$TRIGGER_STATUS" = ACTIVE ] || die "Data Streams trigger did not become ACTIVE"

say "Read-only ingress health check"
HEALTH="$(curl -fsS "$IURL")" || die "Ingress HTTPS health check failed"
printf %s "$HEALTH" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ok") is True and d.get("read_only") is True and d.get("max_token_present") is False,d' \
  || die "Ingress health contract failed"

say "End-to-end synthetic event: ingress -> Data Streams -> trigger -> worker -> YDB"
NONCE="$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
SYNTHETIC="$(NONCE="$NONCE" python3 - <<'PY'
import json,os
print(json.dumps({'update_type':'__maximum_healthcheck__','nonce':os.environ['NONCE']},separators=(',',':')))
PY
)"
EXPECTED_KEY="$(SYNTHETIC="$SYNTHETIC" python3 - <<'PY'
import hashlib,json,os
u=json.loads(os.environ['SYNTHETIC'])
canonical=json.dumps(u,ensure_ascii=False,sort_keys=True,separators=(',',':'))
print('max:__maximum_healthcheck__:sha256:'+hashlib.sha256(canonical.encode()).hexdigest())
PY
)"
curl -fsS -X POST "$IURL" \
  -H "Content-Type: application/json" \
  -H "X-Max-Bot-Api-Secret: $MAX_WEBHOOK_SECRET" \
  --data-binary "$SYNTHETIC" | grep -q '^OK$' || die "Synthetic ingress POST failed"

FOUND=0
for _ in $(seq 1 180); do
  IAM_TOKEN="$(yc iam create-token)"; export IAM_TOKEN
  if ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" yql \
    -s "SELECT event_id FROM processed_events WHERE event_id = '$EXPECTED_KEY';" \
    >"$TMP/e2e.txt" 2>/dev/null && grep -Fq "$EXPECTED_KEY" "$TMP/e2e.txt"; then
    FOUND=1
    break
  fi
  sleep 2
done
unset IAM_TOKEN
[ "$FOUND" = 1 ] || die "End-to-end event was not confirmed in YDB within 6 minutes"
printf 'End-to-end path: OK\n'

printf '\nYANDEX_FUNCTIONS_INFRA_READY_FOR_CUTOVER\n'
printf 'Ingress: %s\n' "$IURL"
printf 'Transport: Data Streams (1 partition) -> private Cloud Function -> YDB\n'
printf 'DLQ: %s\n' "$DLQ_NAME"
printf 'Docker/Container Registry: NOT USED\n'
printf 'Lockbox: NOT USED\n'
printf 'Persistent static access keys: NONE\n'
printf 'MAX webhook activation: OFF\n'
printf 'Next action is the separate reviewed activate-max-webhook.sh only.\n'
