#!/usr/bin/env bash
set -euo pipefail
umask 077

# MAX bot «МАКСимум мебель» — guarded Yandex Cloud bootstrap.
# Production transport: MAX -> ingress -> Data Streams -> worker -> YDB.
# Yandex Message Queue is used only as the trigger DLQ.
# IMPORTANT: this script NEVER creates, updates or deletes a MAX webhook.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
CLOUD_NAME="maximum-maxbot"
BRANCH="maxbot-production"
REPO="https://github.com/kukla8122-create/portfolio-maxdizain.ru.git"
REGION="ru-central1"
MAX_API="https://platform-api2.max.ru"
MAX_SECRET_NAME="maximum-maxbot-max"
YDS_SECRET_NAME="maximum-maxbot-yds"
STREAM_NAME="maximum-maxbot-events"
DLQ_NAME="maximum-maxbot-dlq"
TRIGGER_NAME="maximum-maxbot-stream-trigger"
LEGACY_TRIGGER_NAME="maximum-maxbot-worker-trigger"
YDS_ENDPOINT="https://yds.serverless.yandexcloud.net"
YMQ_ENDPOINT="https://message-queue.api.cloud.yandex.net"

TMP="$(mktemp -d)"
SRC="$TMP/src"
VENV="$TMP/venv"
TEMP_INGRESS_YMQ=0
NEW_ACCESS_KEY_RESOURCE_ID=""
NEW_ACCESS_KEY_COMMITTED=0

cleanup(){
  if [ "${TEMP_INGRESS_YMQ:-0}" = 1 ] && [ -n "${ING_SA:-}" ]; then
    yc resource-manager folder remove-access-binding "$FOLDER_ID" \
      --role ymq.writer --service-account-id "$ING_SA" >/dev/null 2>&1 || true
  fi
  if [ -n "${NEW_ACCESS_KEY_RESOURCE_ID:-}" ] && [ "${NEW_ACCESS_KEY_COMMITTED:-0}" != 1 ]; then
    yc iam access-key delete "$NEW_ACCESS_KEY_RESOURCE_ID" >/dev/null 2>&1 || true
  fi
  sudo docker logout cr.yandex >/dev/null 2>&1 || true
  rm -rf "$TMP"
  unset MAX_BOT_TOKEN MAX_WEBHOOK_SECRET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY IAM_TOKEN || true
}
trap cleanup EXIT

say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }
getj(){ "$@" --format json 2>/dev/null || true; }
secret_version(){ python3 -c 'import json,sys; d=json.load(sys.stdin); v=d.get("current_version") or d.get("currentVersion") or {}; print(v.get("id", ""))'; }

for x in yc git python3 curl sudo; do command -v "$x" >/dev/null || die "Missing tool: $x"; done
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

say "Verify exact Yandex cloud and folder"
CJ="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FJ="$(yc resource-manager folder get "$FOLDER_ID" --format json)"
[ "$(printf %s "$CJ" | jget id)" = "$CLOUD_ID" ] || die "Wrong cloud id"
[ "$(printf %s "$CJ" | jget name)" = "$CLOUD_NAME" ] || die "Wrong cloud name"
# Cloud.Get no longer exposes a cloud status field. Folder.Get still does.
[ "$(printf %s "$FJ" | jget id)" = "$FOLDER_ID" ] || die "Wrong folder id"
[ "$(printf %s "$FJ" | jget cloud_id)" = "$CLOUD_ID" ] || die "Folder belongs to another cloud"
[ "$(printf %s "$FJ" | jget status)" = ACTIVE ] || die "Folder is not ACTIVE"
say "Cloud identity verified; folder is ACTIVE"

say "Prepare Cloud Shell build tools"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io
fi
sudo service docker start >/dev/null 2>&1 || sudo systemctl start docker >/dev/null 2>&1 || true
if ! sudo docker info >/dev/null 2>&1; then
  sudo nohup dockerd >"$TMP/dockerd.log" 2>&1 &
  for _ in $(seq 1 30); do sudo docker info >/dev/null 2>&1 && break; sleep 1; done
fi
sudo docker info >/dev/null 2>&1 || die "Docker daemon unavailable"

if ! command -v ydb >/dev/null 2>&1; then
  curl -fsSL https://install.ydb.tech/cli | bash
  export PATH="$HOME/ydb/bin:$HOME/.local/bin:$PATH"
  if ! command -v ydb >/dev/null 2>&1; then
    YDB_BIN="$(find "$HOME" -type f -name ydb -perm -u+x 2>/dev/null | head -n 1 || true)"
    [ -n "$YDB_BIN" ] || die "YDB CLI installation completed but executable was not found"
    export PATH="$(dirname "$YDB_BIN"):$PATH"
  fi
fi
command -v ydb >/dev/null 2>&1 || die "YDB CLI unavailable"

say "Clone exact production branch and validate before cloud mutations"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC" >/dev/null 2>&1
cd "$SRC"
if ! python3 -m venv "$VENV" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" -q install --disable-pip-version-check -r requirements-yandex.txt
"$VENV/bin/python" -m py_compile \
  maxbot-selfhosted.py maxbot-selfhosted-yandex.py maxbot-yandex.py \
  maxbot-yandex-production.py maxbot-yandex-split.py maxbot-yandex-stream.py \
  maxbot-yandex-entry.py
"$VENV/bin/python" -m unittest discover -s tests -v
sudo docker build --pull -f Dockerfile.yandex -t maxbot-yandex:bootstrap . >"$TMP/build.log" 2>&1 \
  || { tail -n 100 "$TMP/build.log" >&2; die "Docker build failed"; }
say "Source tests and Docker build passed before provisioning"

ensure_sa(){
  local n="$1" d="$2" j
  j="$(getj yc iam service-account get "$n")"
  if [ -z "$j" ]; then
    yc iam service-account create "$n" --folder-id "$FOLDER_ID" --description "$d" >/dev/null
    j="$(yc iam service-account get "$n" --format json)"
  fi
  printf %s "$j" | jget id
}
grant_folder(){
  yc resource-manager folder add-access-binding "$FOLDER_ID" \
    --role "$2" --service-account-id "$1" >/dev/null
}

say "Create/reuse dedicated service accounts and required roles"
ING_SA="$(ensure_sa maxbot-ingress-sa 'MAX bot public ingress')"
WRK_SA="$(ensure_sa maxbot-worker-sa 'MAX bot private worker')"
TRG_SA="$(ensure_sa maxbot-trigger-sa 'MAX bot ordered Data Streams trigger')"

grant_folder "$ING_SA" yds.writer
grant_folder "$ING_SA" yds.auditor
grant_folder "$ING_SA" container-registry.images.puller
grant_folder "$WRK_SA" ydb.editor
grant_folder "$WRK_SA" container-registry.images.puller
# Current Yandex Data Streams trigger documentation requires yds.admin for the
# stream service account. Keep this broad stream role isolated to the trigger SA.
grant_folder "$TRG_SA" yds.admin
grant_folder "$TRG_SA" ymq.writer

if yc iam role get serverless-containers.containerInvoker >/dev/null 2>&1; then
  INVOKER_ROLE="serverless-containers.containerInvoker"
elif yc iam role get serverless.containers.invoker >/dev/null 2>&1; then
  INVOKER_ROLE="serverless.containers.invoker"
else
  die "Neither current nor legacy Serverless Containers invoker role exists"
fi
printf 'Container invoker role: %s\n' "$INVOKER_ROLE"

say "Create/reuse stable MAX credentials in Lockbox"
MAX_SJ="$(getj yc lockbox secret get "$MAX_SECRET_NAME")"
if [ -z "$MAX_SJ" ]; then
  printf 'Paste MAX_BOT_TOKEN here (input is hidden): '
  read -r -s MAX_BOT_TOKEN
  printf '\n'
  [ -n "$MAX_BOT_TOKEN" ] || die "Empty MAX token"
  MAX_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
  MAX_BOT_TOKEN="$MAX_BOT_TOKEN" MAX_WEBHOOK_SECRET="$MAX_WEBHOOK_SECRET" \
    python3 - <<'PY' >"$TMP/max-payload.json"
import json, os
print(json.dumps([
    {'key': 'max_bot_token', 'text_value': os.environ['MAX_BOT_TOKEN']},
    {'key': 'max_webhook_secret', 'text_value': os.environ['MAX_WEBHOOK_SECRET']},
]))
PY
  yc lockbox secret create --name "$MAX_SECRET_NAME" \
    --description "MAX bot token and stable webhook secret" \
    --payload "$(cat "$TMP/max-payload.json")" \
    --cloud-id "$CLOUD_ID" --folder-id "$FOLDER_ID" --deletion-protection >/dev/null
  MAX_SJ="$(yc lockbox secret get "$MAX_SECRET_NAME" --format json)"
fi
MAX_SID="$(printf %s "$MAX_SJ" | jget id)"
MAX_VER="$(printf %s "$MAX_SJ" | secret_version)"
[ -n "$MAX_SID" ] && [ -n "$MAX_VER" ] || die "Cannot resolve MAX Lockbox secret/version"
MAX_BOT_TOKEN="$(yc lockbox payload get --id "$MAX_SID" --key max_bot_token)"
MAX_WEBHOOK_SECRET="$(yc lockbox payload get --id "$MAX_SID" --key max_webhook_secret)"
[ -n "$MAX_BOT_TOKEN" ] && [ -n "$MAX_WEBHOOK_SECRET" ] || die "MAX Lockbox payload incomplete"
yc lockbox secret add-access-binding "$MAX_SID" --role lockbox.payloadViewer \
  --service-account-id "$ING_SA" >/dev/null
yc lockbox secret add-access-binding "$MAX_SID" --role lockbox.payloadViewer \
  --service-account-id "$WRK_SA" >/dev/null

say "Validate MAX token read-only; subscriptions are not changed"
ME="$(curl -fsS "$MAX_API/me" -H "Authorization: $MAX_BOT_TOKEN")" || die "MAX /me failed"
printf %s "$ME" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("is_bot") is True, d' \
  || die "MAX token does not identify a bot"

say "Create/reuse stable Data Streams access key in Lockbox"
YDS_SJ="$(getj yc lockbox secret get "$YDS_SECRET_NAME")"
if [ -z "$YDS_SJ" ]; then
  yc iam access-key create --service-account-id "$ING_SA" \
    --description maximum-maxbot-ingress-yds --format json >"$TMP/yds-key.json"
  AWS_ACCESS_KEY_ID="$(cat "$TMP/yds-key.json" | jget access_key.key_id)"
  AWS_SECRET_ACCESS_KEY="$(cat "$TMP/yds-key.json" | jget secret)"
  NEW_ACCESS_KEY_RESOURCE_ID="$(cat "$TMP/yds-key.json" | jget access_key.id)"
  [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] \
    || die "Data Streams access-key creation returned incomplete credentials"
  AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
    python3 - <<'PY' >"$TMP/yds-payload.json"
import json, os
print(json.dumps([
    {'key': 'yds_access_key_id', 'text_value': os.environ['AWS_ACCESS_KEY_ID']},
    {'key': 'yds_secret_access_key', 'text_value': os.environ['AWS_SECRET_ACCESS_KEY']},
]))
PY
  yc lockbox secret create --name "$YDS_SECRET_NAME" \
    --description "Stable Data Streams producer credentials for MAX ingress" \
    --payload "$(cat "$TMP/yds-payload.json")" \
    --cloud-id "$CLOUD_ID" --folder-id "$FOLDER_ID" --deletion-protection >/dev/null
  NEW_ACCESS_KEY_COMMITTED=1
  YDS_SJ="$(yc lockbox secret get "$YDS_SECRET_NAME" --format json)"
fi
YDS_SID="$(printf %s "$YDS_SJ" | jget id)"
YDS_VER="$(printf %s "$YDS_SJ" | secret_version)"
[ -n "$YDS_SID" ] && [ -n "$YDS_VER" ] || die "Cannot resolve Data Streams Lockbox secret/version"
AWS_ACCESS_KEY_ID="$(yc lockbox payload get --id "$YDS_SID" --key yds_access_key_id)"
AWS_SECRET_ACCESS_KEY="$(yc lockbox payload get --id "$YDS_SID" --key yds_secret_access_key)"
[ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] || die "Data Streams Lockbox payload incomplete"
yc lockbox secret add-access-binding "$YDS_SID" --role lockbox.payloadViewer \
  --service-account-id "$ING_SA" >/dev/null

say "Create/reuse YDB Serverless database"
YJ="$(getj yc ydb database get maximum-maxbot-db)"
if [ -z "$YJ" ]; then
  yc ydb database create maximum-maxbot-db --folder-id "$FOLDER_ID" \
    --serverless --sls-provisioned-rcu 0 --deletion-protection >/dev/null
fi
YS=""
for _ in $(seq 1 90); do
  YJ="$(yc ydb database get maximum-maxbot-db --format json)"
  YS="$(printf %s "$YJ" | jget status)"
  [ "$YS" = RUNNING ] && break
  [ "$YS" = ERROR ] && die "YDB entered ERROR state"
  sleep 4
done
[ "$YS" = RUNNING ] || die "YDB startup timeout"
YDB_ID="$(printf %s "$YJ" | jget id)"
YDB_CS="$(printf %s "$YJ" | jget endpoint)"
[ -n "$YDB_ID" ] && [ -n "$YDB_CS" ] || die "YDB identity/endpoint missing"

YDB_CS="$YDB_CS" python3 - <<'PY' >"$TMP/ydb-connection"
import os
from urllib.parse import urlsplit, parse_qs
s = os.environ['YDB_CS']
u = urlsplit(s)
endpoint = f'{u.scheme}://{u.netloc}'
path = (parse_qs(u.query).get('database') or [''])[0]
if not endpoint.startswith('grpcs://') or not path.startswith('/'):
    raise SystemExit(f'Cannot parse YDB endpoint: {s}')
print(endpoint)
print(path)
PY
YDB_GRPC="$(sed -n '1p' "$TMP/ydb-connection")"
YDB_PATH="$(sed -n '2p' "$TMP/ydb-connection")"
[ -n "$YDB_GRPC" ] && [ -n "$YDB_PATH" ] || die "Cannot parse YDB connection string"

say "Create/reuse ordered Data Stream: exactly 1 partition, 128 KB/s, 1h retention"
IAM_TOKEN="$(yc iam create-token)"
IAM_TOKEN="$IAM_TOKEN" ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" discovery whoami >/dev/null \
  || die "YDB CLI authentication failed"
if IAM_TOKEN="$IAM_TOKEN" ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" \
  scheme describe "$STREAM_NAME" >"$TMP/stream-before.txt" 2>"$TMP/stream-before.err"; then
  PARTITIONS="$(python3 - "$TMP/stream-before.txt" <<'PY'
import re, sys
s=open(sys.argv[1],encoding='utf-8',errors='replace').read()
m=re.search(r'PartitionsCount:\s*(\d+)',s)
print(m.group(1) if m else '')
PY
)"
  [ "$PARTITIONS" = 1 ] || die "Existing stream has $PARTITIONS partitions; cannot safely reduce to one"
  IAM_TOKEN="$IAM_TOKEN" ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" topic alter \
    --retention-period 1h \
    --partition-write-speed-kbps 128 \
    --metering-mode reserved-capacity \
    --supported-codecs raw \
    "$STREAM_NAME" >/dev/null
else
  IAM_TOKEN="$IAM_TOKEN" ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" topic create \
    --partitions-count 1 \
    --retention-period 1h \
    --partition-write-speed-kbps 128 \
    --metering-mode reserved-capacity \
    --supported-codecs raw \
    "$STREAM_NAME" >/dev/null \
    || { cat "$TMP/stream-before.err" >&2 || true; die "Data Stream topic creation failed"; }
fi
IAM_TOKEN="$IAM_TOKEN" ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" \
  scheme describe "$STREAM_NAME" >"$TMP/stream-after.txt"
PARTITIONS="$(python3 - "$TMP/stream-after.txt" <<'PY'
import re, sys
s=open(sys.argv[1],encoding='utf-8',errors='replace').read()
m=re.search(r'PartitionsCount:\s*(\d+)',s)
print(m.group(1) if m else '')
PY
)"
[ "$PARTITIONS" = 1 ] || die "Data Stream partition verification failed: $PARTITIONS"
unset IAM_TOKEN
YDS_STREAM_ID="/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME"
printf 'Data Stream ID: %s\n' "$YDS_STREAM_ID"

say "Create/reuse Standard YMQ dead-letter queue only"
# The ingress access key already exists; add queue write permission only long enough
# to create/read the DLQ, then remove it. The trigger SA retains ymq.writer at runtime.
grant_folder "$ING_SA" ymq.writer
TEMP_INGRESS_YMQ=1
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  DLQ_NAME="$DLQ_NAME" OUT="$TMP/dlq.json" "$VENV/bin/python" - <<'PYQ'
import boto3, json, os
s=boto3.client(
    'sqs', endpoint_url='https://message-queue.api.cloud.yandex.net', region_name='ru-central1',
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])
try:
    url=s.get_queue_url(QueueName=os.environ['DLQ_NAME'])['QueueUrl']
except Exception:
    url=s.create_queue(
        QueueName=os.environ['DLQ_NAME'],
        Attributes={'MessageRetentionPeriod':'1209600'})['QueueUrl']
s.set_queue_attributes(QueueUrl=url,Attributes={'MessageRetentionPeriod':'1209600'})
a=s.get_queue_attributes(QueueUrl=url,AttributeNames=['QueueArn'])['Attributes']
json.dump({'url':url,'arn':a['QueueArn']},open(os.environ['OUT'],'w'))
PYQ
DLQURL="$(cat "$TMP/dlq.json" | jget url)"
DLQARN="$(cat "$TMP/dlq.json" | jget arn)"
[ -n "$DLQURL" ] && [ -n "$DLQARN" ] || die "DLQ setup incomplete"
yc resource-manager folder remove-access-binding "$FOLDER_ID" \
  --role ymq.writer --service-account-id "$ING_SA" >/dev/null
TEMP_INGRESS_YMQ=0

say "Create/reuse Container Registry and push immutable image"
RJ="$(getj yc container registry get maximum-maxbot-registry)"
if [ -z "$RJ" ]; then
  yc container registry create --name maximum-maxbot-registry --folder-id "$FOLDER_ID" >/dev/null
  RJ="$(yc container registry get maximum-maxbot-registry --format json)"
fi
RID="$(printf %s "$RJ" | jget id)"
[ -n "$RID" ] || die "Container Registry id missing"
IMG="cr.yandex/$RID/maximum-maxbot:2026-08-18-$(git rev-parse --short=12 HEAD)"
yc iam create-token | sudo docker login --username iam --password-stdin cr.yandex >/dev/null 2>&1
sudo docker tag maxbot-yandex:bootstrap "$IMG"
sudo docker push "$IMG" >"$TMP/push.log" 2>&1 \
  || { tail -n 100 "$TMP/push.log" >&2; die "Docker push failed"; }

ensure_container(){
  local n="$1" j
  j="$(getj yc serverless container get "$n")"
  if [ -z "$j" ]; then
    yc serverless container create --name "$n" --folder-id "$FOLDER_ID" >/dev/null
    j="$(yc serverless container get "$n" --format json)"
  fi
  printf %s "$j"
}

say "Create/reuse private worker and public ingress containers"
WJ="$(ensure_container maximum-maxbot-worker)"
IJ="$(ensure_container maximum-maxbot-ingress)"
WID="$(printf %s "$WJ" | jget id)"
IID="$(printf %s "$IJ" | jget id)"
WURL="$(printf %s "$WJ" | jget url)"
IURL="$(printf %s "$IJ" | jget url)"
[ -n "$WID" ] && [ -n "$IID" ] && [ -n "$WURL" ] && [ -n "$IURL" ] \
  || die "Container identity/URL missing"
IURL="${IURL%/}"
WURL="${WURL%/}"
yc serverless container deny-unauthenticated-invoke maximum-maxbot-worker >/dev/null 2>&1 || true
yc serverless container add-access-binding --name maximum-maxbot-worker \
  --service-account-id "$TRG_SA" --role "$INVOKER_ROLE" >/dev/null

say "Deploy private worker with YDB and Lockbox secrets"
yc serverless container revision deploy \
  --container-name maximum-maxbot-worker \
  --image "$IMG" \
  --service-account-id "$WRK_SA" \
  --runtime http --cores 1 --memory 256MB --execution-timeout 30s \
  --concurrency 1 --min-instances 0 \
  --environment "APP_MODE=worker,YDB_CONNECTION_STRING=$YDB_CS" \
  --secret "environment-variable=MAX_BOT_TOKEN,id=$MAX_SID,version-id=$MAX_VER,key=max_bot_token" \
  --secret "environment-variable=MAX_WEBHOOK_SECRET,id=$MAX_SID,version-id=$MAX_VER,key=max_webhook_secret" \
  >"$TMP/deploy-worker.log" 2>&1 \
  || { tail -n 120 "$TMP/deploy-worker.log" >&2; die "Worker deploy failed"; }

say "Deploy public ingress with ordered Data Streams; MAX webhook remains OFF"
yc serverless container revision deploy \
  --container-name maximum-maxbot-ingress \
  --image "$IMG" \
  --service-account-id "$ING_SA" \
  --runtime http --cores 1 --memory 256MB --execution-timeout 10s \
  --concurrency 1 --min-instances 0 \
  --environment "APP_MODE=ingress,PUBLIC_BASE_URL=$IURL,YDS_ENDPOINT=$YDS_ENDPOINT,YDS_STREAM_NAME=$YDS_STREAM_ID,YDS_REGION=$REGION" \
  --secret "environment-variable=MAX_BOT_TOKEN,id=$MAX_SID,version-id=$MAX_VER,key=max_bot_token" \
  --secret "environment-variable=MAX_WEBHOOK_SECRET,id=$MAX_SID,version-id=$MAX_VER,key=max_webhook_secret" \
  --secret "environment-variable=AWS_ACCESS_KEY_ID,id=$YDS_SID,version-id=$YDS_VER,key=yds_access_key_id" \
  --secret "environment-variable=AWS_SECRET_ACCESS_KEY,id=$YDS_SID,version-id=$YDS_VER,key=yds_secret_access_key" \
  >"$TMP/deploy-ingress.log" 2>&1 \
  || { tail -n 120 "$TMP/deploy-ingress.log" >&2; die "Ingress deploy failed"; }
yc serverless container allow-unauthenticated-invoke maximum-maxbot-ingress >/dev/null

say "Disable exact legacy YMQ source trigger if an older partial deployment left it behind"
if yc serverless trigger get "$LEGACY_TRIGGER_NAME" >/dev/null 2>&1; then
  yc serverless trigger pause "$LEGACY_TRIGGER_NAME" >/dev/null 2>&1 || true
  printf 'Legacy trigger %s is paused and is NOT part of production.\n' "$LEGACY_TRIGGER_NAME"
fi

say "Create/update Data Streams trigger -> private worker with DLQ"
TJ="$(getj yc serverless trigger get "$TRIGGER_NAME")"
if [ -z "$TJ" ]; then
  yc serverless trigger create yds \
    --name "$TRIGGER_NAME" \
    --database "$YDB_PATH" \
    --stream "$STREAM_NAME" \
    --batch-size 1b --batch-cutoff 1s \
    --stream-service-account-id "$TRG_SA" \
    --invoke-container-id "$WID" \
    --invoke-container-service-account-id "$TRG_SA" \
    --retry-attempts 5 --retry-interval 10s \
    --dlq-queue-id "$DLQARN" \
    --dlq-service-account-id "$TRG_SA" >/dev/null
else
  yc serverless trigger update yds "$TRIGGER_NAME" \
    --new-database "$YDB_PATH" \
    --new-stream "$STREAM_NAME" \
    --new-batch-size 1b --new-batch-cutoff 1s \
    --new-stream-service-account-id "$TRG_SA" \
    --new-invoke-container-id "$WID" \
    --new-invoke-container-service-account-id "$TRG_SA" \
    --new-container-retry-attempts 5 --new-container-retry-interval 10s \
    --new-container-dlq-queue-id "$DLQARN" \
    --new-container-dlq-service-account-id "$TRG_SA" >/dev/null
fi
for _ in $(seq 1 30); do
  TJ="$(yc serverless trigger get "$TRIGGER_NAME" --format json)"
  TS="$(printf %s "$TJ" | jget status)"
  [ "$TS" = ACTIVE ] && break
  sleep 2
done
[ "${TS:-}" = ACTIVE ] || die "Data Streams trigger is not ACTIVE"

say "Read-only ingress readiness and route isolation"
for _ in $(seq 1 50); do
  curl -fsS "$IURL/health" >"$TMP/ih.json" 2>/dev/null && break
  sleep 2
done
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("ok") and d.get("mode")=="ingress" and d.get("transport")=="data-streams", d' "$TMP/ih.json" \
  || die "Ingress health failed"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$IURL/trigger")" = 404 ] \
  || die "Ingress route isolation failed"
curl -fsS "$IURL/ready" >"$TMP/ir.json" || die "Ingress readiness failed"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("ok") and d.get("transport")=="data-streams" and d.get("max_api") and d.get("stream") and d.get("stream_status")=="ACTIVE" and d.get("read_only") is True and d.get("activation_enabled") is False and d.get("public_url_configured") is True, d' "$TMP/ir.json" \
  || die "Ingress readiness contract failed"

say "Private worker readiness and route isolation"
C=""
for _ in $(seq 1 50); do
  IAM_TOKEN="$(yc iam create-token)"
  C="$(curl -sS -o "$TMP/wh.json" -w '%{http_code}' \
    -H "Authorization: Bearer $IAM_TOKEN" "$WURL/health" || true)"
  [ "$C" = 200 ] && break
  sleep 2
done
[ "$C" = 200 ] || die "Private worker health failed"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $IAM_TOKEN" "$WURL/webhook")" = 404 ] \
  || die "Worker route isolation failed"
curl -fsS -H "Authorization: Bearer $IAM_TOKEN" "$WURL/ready" >"$TMP/wr.json" \
  || die "Worker readiness failed"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("ok") and d.get("transport")=="data-streams" and d.get("max_api") and d.get("storage"), d' "$TMP/wr.json" \
  || die "Worker readiness contract failed"
unset IAM_TOKEN

say "End-to-end Data Streams -> trigger -> worker -> YDB probe"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  YDS_STREAM_ID="$YDS_STREAM_ID" OUT="$TMP/probe" "$VENV/bin/python" - <<'PYP'
import boto3, hashlib, json, os, time, uuid
u={'update_type':'maximum_bootstrap_probe','probe_id':str(uuid.uuid4()),'ts':int(time.time())}
c=json.dumps(u,ensure_ascii=False,sort_keys=True,separators=(',',':'))
k='max:maximum_bootstrap_probe:sha256:'+hashlib.sha256(c.encode()).hexdigest()
s=boto3.client(
    'kinesis', endpoint_url='https://yds.serverless.yandexcloud.net', region_name='ru-central1',
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])
s.put_record(StreamName=os.environ['YDS_STREAM_ID'],Data=json.dumps(u,ensure_ascii=False).encode(),PartitionKey='bootstrap-probe')
open(os.environ['OUT'],'w').write(k)
PYP
PROBE="$(cat "$TMP/probe")"
OK=0
# Yandex documents that a newly created Data Streams trigger can take up to five
# minutes to start. Give the first bootstrap enough time without activating MAX.
for _ in $(seq 1 105); do
  IAM_TOKEN="$(yc iam create-token)"
  if YDB_CONNECTION_STRING="$YDB_CS" YDB_ACCESS_TOKEN_CREDENTIALS="$IAM_TOKEN" \
    PROBE="$PROBE" "$VENV/bin/python" - <<'PYDB'
import os, ydb
d=ydb.Driver(connection_string=os.environ['YDB_CONNECTION_STRING'],credentials=ydb.credentials_from_env_variables())
try:
    d.wait(fail_fast=True,timeout=7)
    p=ydb.QuerySessionPool(d,size=1)
    r=p.execute_with_retries(
        'DECLARE $id AS Utf8; SELECT event_id FROM processed_events WHERE event_id=$id LIMIT 1;',
        {'$id':os.environ['PROBE']},
        retry_settings=ydb.RetrySettings(max_retries=3,idempotent=True))
    raise SystemExit(0 if r and r[0].rows else 3)
finally:
    d.stop(timeout=5)
PYDB
  then
    OK=1
    unset IAM_TOKEN
    break
  fi
  unset IAM_TOKEN
  sleep 4
done
[ "$OK" = 1 ] || die "Ordered trigger probe was not found in YDB within seven minutes"

printf '\nYANDEX_INFRA_READY_FOR_CUTOVER\nIngress: %s\nWorker: private\nYDB: maximum-maxbot-db RUNNING\nTransport: Data Streams %s (1 partition, 128 KB/s, 1h retention)\nDLQ: %s Standard\nTrigger: %s ACTIVE\nSecrets: Lockbox\nMAX webhook activation: OFF\nNext production action: deploy/activate-max-webhook.sh (explicit only)\n' \
  "$IURL" "$STREAM_NAME" "$DLQ_NAME" "$TRIGGER_NAME"
