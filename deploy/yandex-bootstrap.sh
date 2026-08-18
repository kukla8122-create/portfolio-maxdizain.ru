#!/usr/bin/env bash
set -euo pipefail
umask 077

# MAX bot «МАКСимум мебель» — guarded Yandex Cloud bootstrap.
# Creates/updates infrastructure and validates it, but NEVER activates MAX Webhook.
# Production cutover is a separate explicit deploy/activate-max-webhook.sh action.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
CLOUD_NAME="maximum-maxbot"
BRANCH="maxbot-production"
REPO="https://github.com/kukla8122-create/portfolio-maxdizain.ru.git"
REGION="ru-central1"
MAX_API="https://platform-api2.max.ru"
MAX_SECRET_NAME="maximum-maxbot-max"
YMQ_SECRET_NAME="maximum-maxbot-ymq"
QUEUE_NAME="maximum-maxbot-events"
DLQ_NAME="maximum-maxbot-dlq"

TMP="$(mktemp -d)"
SRC="$TMP/src"
VENV="$TMP/venv"
TEMP_YMQ_ADMIN=0
NEW_ACCESS_KEY_RESOURCE_ID=""
NEW_ACCESS_KEY_COMMITTED=0

cleanup(){
  if [ "${TEMP_YMQ_ADMIN:-0}" = 1 ] && [ -n "${ING_SA:-}" ]; then
    yc resource-manager folder remove-access-binding "$FOLDER_ID" \
      --role ymq.admin --service-account-id "$ING_SA" >/dev/null 2>&1 || true
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
secret_version(){ python3 -c 'import json,sys;d=json.load(sys.stdin);v=d.get("current_version") or d.get("currentVersion") or {};print(v.get("id", ""))'; }

for x in yc git python3 curl sudo; do command -v "$x" >/dev/null || die "Missing tool: $x"; done

yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

say "Verify exact cloud and folder"
CJ="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FJ="$(yc resource-manager folder get "$FOLDER_ID" --format json)"
[ "$(printf %s "$CJ" | jget id)" = "$CLOUD_ID" ] || die "Wrong cloud id"
[ "$(printf %s "$CJ" | jget name)" = "$CLOUD_NAME" ] || die "Wrong cloud name"
# Cloud.Get no longer exposes a cloud status field. Folder.Get still does.
[ "$(printf %s "$FJ" | jget id)" = "$FOLDER_ID" ] || die "Wrong folder id"
[ "$(printf %s "$FJ" | jget cloud_id)" = "$CLOUD_ID" ] || die "Folder belongs to another cloud"
[ "$(printf %s "$FJ" | jget status)" = ACTIVE ] || die "Folder is not ACTIVE"
say "Cloud identity verified; folder is ACTIVE"

say "Prepare local Cloud Shell build tools"
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

say "Validate source, tests and Docker image before cloud provisioning"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC" >/dev/null 2>&1
cd "$SRC"
python3 -m py_compile \
  maxbot-selfhosted.py maxbot-selfhosted-yandex.py maxbot-yandex.py \
  maxbot-yandex-production.py maxbot-yandex-split.py maxbot-yandex-entry.py
python3 -m unittest discover -s tests -v
if ! python3 -m venv "$VENV" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" -q install --disable-pip-version-check -r requirements-yandex.txt
sudo docker build --pull -f Dockerfile.yandex -t maxbot-yandex:bootstrap . >"$TMP/build.log" 2>&1 \
  || { tail -n 80 "$TMP/build.log" >&2; die "Docker build failed"; }

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

say "Service accounts with current service-specific/trigger-required roles"
ING_SA="$(ensure_sa maxbot-ingress-sa 'MAX bot public ingress')"
WRK_SA="$(ensure_sa maxbot-worker-sa 'MAX bot private worker')"
TRG_SA="$(ensure_sa maxbot-trigger-sa 'MAX bot YMQ trigger')"
grant_folder "$ING_SA" ymq.writer
grant_folder "$ING_SA" container-registry.images.puller
grant_folder "$WRK_SA" ydb.editor
grant_folder "$WRK_SA" container-registry.images.puller
# Yandex's current Serverless Containers documentation for a Message Queue
# trigger explicitly requires editor on the folder containing the source queue.
# Keep that broad requirement isolated to this dedicated trigger SA; it has no
# static access key and is not used by the application containers themselves.
grant_folder "$TRG_SA" editor

say "Stable MAX credentials in Lockbox"
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
  yc lockbox secret create \
    --name "$MAX_SECRET_NAME" \
    --description "MAX bot token and stable webhook secret" \
    --payload "$(cat "$TMP/max-payload.json")" \
    --cloud-id "$CLOUD_ID" --folder-id "$FOLDER_ID" --deletion-protection >/dev/null
  MAX_SJ="$(yc lockbox secret get "$MAX_SECRET_NAME" --format json)"
else
  MAX_SID="$(printf %s "$MAX_SJ" | jget id)"
  MAX_BOT_TOKEN="$(yc lockbox payload get --id "$MAX_SID" --key max_bot_token)"
  MAX_WEBHOOK_SECRET="$(yc lockbox payload get --id "$MAX_SID" --key max_webhook_secret)"
fi
MAX_SID="$(printf %s "$MAX_SJ" | jget id)"
MAX_VER="$(printf %s "$MAX_SJ" | secret_version)"
[ -n "$MAX_SID" ] && [ -n "$MAX_VER" ] || die "Cannot resolve MAX Lockbox secret/version"
[ -n "${MAX_BOT_TOKEN:-}" ] || MAX_BOT_TOKEN="$(yc lockbox payload get --id "$MAX_SID" --key max_bot_token)"
[ -n "${MAX_WEBHOOK_SECRET:-}" ] || MAX_WEBHOOK_SECRET="$(yc lockbox payload get --id "$MAX_SID" --key max_webhook_secret)"
[ -n "$MAX_BOT_TOKEN" ] && [ -n "$MAX_WEBHOOK_SECRET" ] || die "MAX Lockbox payload incomplete"

yc lockbox secret add-access-binding "$MAX_SID" --role lockbox.payloadViewer \
  --service-account-id "$ING_SA" >/dev/null
yc lockbox secret add-access-binding "$MAX_SID" --role lockbox.payloadViewer \
  --service-account-id "$WRK_SA" >/dev/null

say "Validate MAX token without changing any subscription"
ME="$(curl -fsS "$MAX_API/me" -H "Authorization: $MAX_BOT_TOKEN")" || die "MAX /me failed"
printf %s "$ME" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d.get("is_bot") is True,d' \
  || die "MAX token does not identify a bot"

say "Stable YMQ producer key in a separate Lockbox secret"
# Temporarily elevate only the ingress SA for queue creation/RedrivePolicy setup.
grant_folder "$ING_SA" ymq.admin
TEMP_YMQ_ADMIN=1
YMQ_SJ="$(getj yc lockbox secret get "$YMQ_SECRET_NAME")"
if [ -z "$YMQ_SJ" ]; then
  yc iam access-key create --service-account-id "$ING_SA" \
    --description maximum-maxbot-ingress-ymq --format json >"$TMP/key.json"
  AWS_ACCESS_KEY_ID="$(cat "$TMP/key.json" | jget access_key.key_id)"
  AWS_SECRET_ACCESS_KEY="$(cat "$TMP/key.json" | jget secret)"
  NEW_ACCESS_KEY_RESOURCE_ID="$(cat "$TMP/key.json" | jget access_key.id)"
  [ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] \
    || die "YMQ access-key creation returned incomplete credentials"
  AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
    python3 - <<'PY' >"$TMP/ymq-payload.json"
import json, os
print(json.dumps([
    {'key': 'ymq_access_key_id', 'text_value': os.environ['AWS_ACCESS_KEY_ID']},
    {'key': 'ymq_secret_access_key', 'text_value': os.environ['AWS_SECRET_ACCESS_KEY']},
]))
PY
  yc lockbox secret create \
    --name "$YMQ_SECRET_NAME" \
    --description "Stable YMQ producer credentials for MAX ingress" \
    --payload "$(cat "$TMP/ymq-payload.json")" \
    --cloud-id "$CLOUD_ID" --folder-id "$FOLDER_ID" --deletion-protection >/dev/null
  NEW_ACCESS_KEY_COMMITTED=1
  YMQ_SJ="$(yc lockbox secret get "$YMQ_SECRET_NAME" --format json)"
else
  YMQ_SID="$(printf %s "$YMQ_SJ" | jget id)"
  AWS_ACCESS_KEY_ID="$(yc lockbox payload get --id "$YMQ_SID" --key ymq_access_key_id)"
  AWS_SECRET_ACCESS_KEY="$(yc lockbox payload get --id "$YMQ_SID" --key ymq_secret_access_key)"
fi
YMQ_SID="$(printf %s "$YMQ_SJ" | jget id)"
YMQ_VER="$(printf %s "$YMQ_SJ" | secret_version)"
[ -n "$YMQ_SID" ] && [ -n "$YMQ_VER" ] || die "Cannot resolve YMQ Lockbox secret/version"
[ -n "${AWS_ACCESS_KEY_ID:-}" ] || AWS_ACCESS_KEY_ID="$(yc lockbox payload get --id "$YMQ_SID" --key ymq_access_key_id)"
[ -n "${AWS_SECRET_ACCESS_KEY:-}" ] || AWS_SECRET_ACCESS_KEY="$(yc lockbox payload get --id "$YMQ_SID" --key ymq_secret_access_key)"
[ -n "$AWS_ACCESS_KEY_ID" ] && [ -n "$AWS_SECRET_ACCESS_KEY" ] || die "YMQ Lockbox payload incomplete"
yc lockbox secret add-access-binding "$YMQ_SID" --role lockbox.payloadViewer \
  --service-account-id "$ING_SA" >/dev/null

say "Standard YMQ source queue + Standard DLQ + RedrivePolicy"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  QUEUE_NAME="$QUEUE_NAME" DLQ_NAME="$DLQ_NAME" OUT="$TMP/q.json" \
  "$VENV/bin/python" - <<'PYQ'
import boto3, json, os
s = boto3.client(
    'sqs',
    endpoint_url='https://message-queue.api.cloud.yandex.net',
    region_name='ru-central1',
    aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
)
def ensure(name, attrs):
    try:
        return s.get_queue_url(QueueName=name)['QueueUrl']
    except Exception:
        return s.create_queue(QueueName=name, Attributes=attrs)['QueueUrl']

dlq = ensure(os.environ['DLQ_NAME'], {
    'MessageRetentionPeriod': '1209600',
})
dlq_attrs = s.get_queue_attributes(QueueUrl=dlq, AttributeNames=['QueueArn'])['Attributes']
dlq_arn = dlq_attrs['QueueArn']
source = ensure(os.environ['QUEUE_NAME'], {
    'VisibilityTimeout': '60',
    'MessageRetentionPeriod': '345600',
})
redrive = json.dumps({'deadLetterTargetArn': dlq_arn, 'maxReceiveCount': '5'})
s.set_queue_attributes(QueueUrl=source, Attributes={
    'VisibilityTimeout': '60',
    'MessageRetentionPeriod': '345600',
    'RedrivePolicy': redrive,
})
attrs = s.get_queue_attributes(
    QueueUrl=source, AttributeNames=['QueueArn', 'RedrivePolicy', 'VisibilityTimeout']
)['Attributes']
json.dump({
    'url': source,
    'arn': attrs['QueueArn'],
    'dlq_url': dlq,
    'dlq_arn': dlq_arn,
    'redrive': attrs.get('RedrivePolicy', ''),
}, open(os.environ['OUT'], 'w'))
PYQ
QURL="$(cat "$TMP/q.json" | jget url)"
QARN="$(cat "$TMP/q.json" | jget arn)"
DLQURL="$(cat "$TMP/q.json" | jget dlq_url)"
[ -n "$QURL" ] && [ -n "$QARN" ] && [ -n "$DLQURL" ] || die "YMQ queue setup incomplete"
# Runtime only needs writer; remove temporary queue-admin immediately.
yc resource-manager folder remove-access-binding "$FOLDER_ID" \
  --role ymq.admin --service-account-id "$ING_SA" >/dev/null
TEMP_YMQ_ADMIN=0

say "Container Registry and immutable image tag"
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
  || { tail -n 80 "$TMP/push.log" >&2; die "Docker push failed"; }

say "YDB Serverless"
YJ="$(getj yc ydb database get maximum-maxbot-db)"
if [ -z "$YJ" ]; then
  yc ydb database create maximum-maxbot-db --folder-id "$FOLDER_ID" \
    --serverless --sls-provisioned-rcu 0 >/dev/null
fi
for _ in $(seq 1 90); do
  YJ="$(yc ydb database get maximum-maxbot-db --format json)"
  YS="$(printf %s "$YJ" | jget status)"
  [ "$YS" = RUNNING ] && break
  [ "$YS" = ERROR ] && die "YDB entered ERROR state"
  sleep 4
done
[ "${YS:-}" = RUNNING ] || die "YDB startup timeout"
YDB_CS="$(printf %s "$YJ" | jget endpoint)"
[ -n "$YDB_CS" ] || die "YDB endpoint missing"

ensure_container(){
  local n="$1" j
  j="$(getj yc serverless container get "$n")"
  if [ -z "$j" ]; then
    yc serverless container create --name "$n" --folder-id "$FOLDER_ID" >/dev/null
    j="$(yc serverless container get "$n" --format json)"
  fi
  printf %s "$j"
}

say "Create private worker and public ingress container shells"
WJ="$(ensure_container maximum-maxbot-worker)"
IJ="$(ensure_container maximum-maxbot-ingress)"
WID="$(printf %s "$WJ" | jget id)"
IID="$(printf %s "$IJ" | jget id)"
WURL="$(printf %s "$WJ" | jget url)"
IURL="$(printf %s "$IJ" | jget url)"
[ -n "$WID" ] && [ -n "$IID" ] && [ -n "$WURL" ] && [ -n "$IURL" ] \
  || die "Container identity/URL missing"

yc serverless container deny-unauthenticated-invoke maximum-maxbot-worker >/dev/null 2>&1 || true
yc serverless container add-access-binding --name maximum-maxbot-worker \
  --service-account-id "$TRG_SA" --role serverless-containers.containerInvoker >/dev/null

say "Deploy private worker with Lockbox MAX credentials"
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
  || { tail -n 100 "$TMP/deploy-worker.log" >&2; die "Worker deploy failed"; }

say "Deploy public ingress with Lockbox credentials; MAX webhook remains OFF"
yc serverless container revision deploy \
  --container-name maximum-maxbot-ingress \
  --image "$IMG" \
  --service-account-id "$ING_SA" \
  --runtime http --cores 1 --memory 256MB --execution-timeout 10s \
  --concurrency 4 --min-instances 0 \
  --environment "APP_MODE=ingress,PUBLIC_BASE_URL=${IURL%/},YMQ_ENDPOINT=https://message-queue.api.cloud.yandex.net,YMQ_QUEUE_URL=$QURL,YMQ_REGION=$REGION" \
  --secret "environment-variable=MAX_BOT_TOKEN,id=$MAX_SID,version-id=$MAX_VER,key=max_bot_token" \
  --secret "environment-variable=MAX_WEBHOOK_SECRET,id=$MAX_SID,version-id=$MAX_VER,key=max_webhook_secret" \
  --secret "environment-variable=AWS_ACCESS_KEY_ID,id=$YMQ_SID,version-id=$YMQ_VER,key=ymq_access_key_id" \
  --secret "environment-variable=AWS_SECRET_ACCESS_KEY,id=$YMQ_SID,version-id=$YMQ_VER,key=ymq_secret_access_key" \
  >"$TMP/deploy-ingress.log" 2>&1 \
  || { tail -n 100 "$TMP/deploy-ingress.log" >&2; die "Ingress deploy failed"; }
yc serverless container allow-unauthenticated-invoke maximum-maxbot-ingress >/dev/null

say "YMQ trigger to private worker"
TJ="$(getj yc serverless trigger get maximum-maxbot-worker-trigger)"
if [ -z "$TJ" ]; then
  yc serverless trigger create message-queue \
    --name maximum-maxbot-worker-trigger \
    --queue "$QARN" \
    --queue-service-account-id "$TRG_SA" \
    --invoke-container-id "$WID" \
    --invoke-container-service-account-id "$TRG_SA" \
    --batch-size 1 --batch-cutoff 1s >/dev/null
else
  say "Trigger already exists; preserving it instead of creating a duplicate"
fi

say "Ingress readiness and route isolation (read-only)"
for _ in $(seq 1 45); do
  curl -fsS "${IURL%/}/health" >"$TMP/ih.json" 2>/dev/null && break
  sleep 2
done
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("mode")=="ingress"' "$TMP/ih.json" \
  || die "Ingress health failed"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${IURL%/}/trigger")" = 404 ] \
  || die "Ingress route isolation failed"
curl -fsS "${IURL%/}/ready" >"$TMP/ir.json" || die "Ingress readiness failed"
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("max_api") and d.get("queue") and d.get("read_only") is True and d.get("activation_enabled") is False' "$TMP/ir.json" \
  || die "Ingress readiness contract failed"

say "Private worker readiness and route isolation"
IAM_TOKEN="$(yc iam create-token)"
C=""
for _ in $(seq 1 45); do
  C="$(curl -sS -o "$TMP/wh.json" -w '%{http_code}' \
    -H "Authorization: Bearer $IAM_TOKEN" "${WURL%/}/health" || true)"
  [ "$C" = 200 ] && break
  sleep 2
done
[ "$C" = 200 ] || die "Private worker health failed"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $IAM_TOKEN" "${WURL%/}/webhook")" = 404 ] \
  || die "Worker route isolation failed"
curl -fsS -H "Authorization: Bearer $IAM_TOKEN" "${WURL%/}/ready" >"$TMP/wr.json" \
  || die "Worker readiness failed"
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("max_api") and d.get("storage")' "$TMP/wr.json" \
  || die "Worker readiness contract failed"
unset IAM_TOKEN

say "End-to-end YMQ -> trigger -> worker -> YDB probe"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  QURL="$QURL" OUT="$TMP/probe" "$VENV/bin/python" - <<'PYP'
import boto3, hashlib, json, os, time, uuid
u = {'update_type': 'maximum_bootstrap_probe', 'probe_id': str(uuid.uuid4()), 'ts': int(time.time())}
c = json.dumps(u, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
k = 'max:maximum_bootstrap_probe:sha256:' + hashlib.sha256(c.encode()).hexdigest()
s = boto3.client(
    'sqs', endpoint_url='https://message-queue.api.cloud.yandex.net',
    region_name='ru-central1', aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])
s.send_message(QueueUrl=os.environ['QURL'], MessageBody=json.dumps(u, ensure_ascii=False))
open(os.environ['OUT'], 'w').write(k)
PYP
PROBE="$(cat "$TMP/probe")"
OK=0
for _ in $(seq 1 75); do
  IAM_TOKEN="$(yc iam create-token)"
  if YDB_CONNECTION_STRING="$YDB_CS" YDB_ACCESS_TOKEN_CREDENTIALS="$IAM_TOKEN" \
    PROBE="$PROBE" "$VENV/bin/python" - <<'PYDB'
import os, ydb
d = ydb.Driver(
    connection_string=os.environ['YDB_CONNECTION_STRING'],
    credentials=ydb.credentials_from_env_variables())
try:
    d.wait(fail_fast=True, timeout=7)
    p = ydb.QuerySessionPool(d, size=1)
    r = p.execute_with_retries(
        'DECLARE $id AS Utf8; SELECT event_id FROM processed_events WHERE event_id=$id LIMIT 1;',
        {'$id': os.environ['PROBE']},
        retry_settings=ydb.RetrySettings(max_retries=3, idempotent=True))
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
[ "$OK" = 1 ] || die "Trigger probe was not found in YDB within five minutes"

printf '\nYANDEX_INFRA_READY_FOR_CUTOVER\nIngress: %s\nWorker: private\nYDB: maximum-maxbot-db RUNNING\nQueue: %s Standard\nDLQ: %s Standard\nTrigger: present\nSecrets: Lockbox\nMAX webhook activation: OFF\nNext production action: deploy/activate-max-webhook.sh (explicit only)\n' \
  "$IURL" "$QUEUE_NAME" "$DLQ_NAME"
