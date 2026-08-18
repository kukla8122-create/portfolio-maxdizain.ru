#!/usr/bin/env bash
set -euo pipefail
umask 077

# MAX bot «МАКСимум мебель» — Yandex Cloud infrastructure bootstrap.
# Preflight only: MAX webhook is deliberately NOT activated here.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
CLOUD_NAME="maximum-maxbot"
BRANCH="maxbot-production"
REPO="https://github.com/kukla8122-create/portfolio-maxdizain.ru.git"
REGION="ru-central1"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; unset MAX_BOT_TOKEN MAX_WEBHOOK_SECRET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY IAM_TOKEN || true' EXIT
SRC="$TMP/src"; VENV="$TMP/venv"
say(){ printf '\n==> %s\n' "$*"; }
die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); cur=d
for p in sys.argv[1].split("."): cur=cur.get(p,"") if isinstance(cur,dict) else ""
print(json.dumps(cur,ensure_ascii=False) if isinstance(cur,(dict,list)) else ("" if cur is None else cur))' "$1"; }
getj(){ "$@" --format json 2>/dev/null || true; }

for x in yc git python3 curl sudo; do command -v "$x" >/dev/null || die "Missing tool: $x"; done
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null
CJ="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FJ="$(yc resource-manager folder get "$FOLDER_ID" --format json)"
[ "$(printf %s "$CJ"|jget name)" = "$CLOUD_NAME" ] || die "Wrong cloud"
[ "$(printf %s "$CJ"|jget status)" = ACTIVE ] || die "Cloud is not ACTIVE"
[ "$(printf %s "$FJ"|jget status)" = ACTIVE ] || die "Folder is not ACTIVE"
say "Cloud and folder are ACTIVE"

if ! command -v docker >/dev/null 2>&1; then sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io; fi
sudo service docker start >/dev/null 2>&1 || sudo systemctl start docker >/dev/null 2>&1 || true
if ! sudo docker info >/dev/null 2>&1; then sudo nohup dockerd >"$TMP/dockerd.log" 2>&1 & for _ in $(seq 1 30); do sudo docker info >/dev/null 2>&1 && break; sleep 1; done; fi
sudo docker info >/dev/null 2>&1 || die "Docker daemon unavailable"

say "Validate exact GitHub branch before cloud mutations"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$SRC" >/dev/null 2>&1
cd "$SRC"
python3 -m py_compile maxbot-selfhosted.py maxbot-selfhosted-yandex.py maxbot-yandex.py maxbot-yandex-production.py maxbot-yandex-split.py maxbot-yandex-entry.py
python3 -m unittest discover -s tests -v
if ! python3 -m venv "$VENV" >/dev/null 2>&1; then sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv; python3 -m venv "$VENV"; fi
"$VENV/bin/pip" -q install --disable-pip-version-check -r requirements-yandex.txt
sudo docker build --pull -f Dockerfile.yandex -t maxbot-yandex:bootstrap . >"$TMP/build.log" 2>&1 || { tail -n 60 "$TMP/build.log" >&2; die "Docker build failed"; }

say "MAX token: paste only here; input is hidden"
read -r -s -p "MAX_BOT_TOKEN: " MAX_BOT_TOKEN; printf '\n'
[ -n "$MAX_BOT_TOKEN" ] || die "Empty MAX token"
MAX_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"

ensure_sa(){ local n="$1" d="$2" j; j="$(getj yc iam service-account get "$n")"; [ -n "$j" ] || { yc iam service-account create "$n" --folder-id "$FOLDER_ID" --description "$d" >/dev/null; j="$(yc iam service-account get "$n" --format json)"; }; printf %s "$j"|jget id; }
grant(){ yc resource-manager folder add-access-binding "$FOLDER_ID" --role "$2" --service-account-id "$1" >/dev/null; }
ING_SA="$(ensure_sa maxbot-ingress-sa 'MAX bot ingress')"; WRK_SA="$(ensure_sa maxbot-worker-sa 'MAX bot worker')"; TRG_SA="$(ensure_sa maxbot-trigger-sa 'MAX bot YMQ trigger')"
grant "$ING_SA" ymq.writer; grant "$ING_SA" container-registry.images.puller
grant "$WRK_SA" ydb.editor; grant "$WRK_SA" container-registry.images.puller
grant "$TRG_SA" editor

say "Container Registry + image"
RJ="$(getj yc container registry get maximum-maxbot-registry)"; [ -n "$RJ" ] || { yc container registry create --name maximum-maxbot-registry --folder-id "$FOLDER_ID" >/dev/null; RJ="$(yc container registry get maximum-maxbot-registry --format json)"; }
RID="$(printf %s "$RJ"|jget id)"; IMG="cr.yandex/$RID/maximum-maxbot:2026-08-18-$(git rev-parse --short=12 HEAD)"
yc iam create-token | sudo docker login --username iam --password-stdin cr.yandex >/dev/null 2>&1
sudo docker tag maxbot-yandex:bootstrap "$IMG"; sudo docker push "$IMG" >"$TMP/push.log" 2>&1 || { tail -n 60 "$TMP/push.log" >&2; die "Docker push failed"; }

say "YDB Serverless, provisioned RCU=0"
YJ="$(getj yc ydb database get maximum-maxbot-db)"; [ -n "$YJ" ] || yc ydb database create maximum-maxbot-db --folder-id "$FOLDER_ID" --serverless --sls-provisioned-rcu 0 >/dev/null
for _ in $(seq 1 90); do YJ="$(yc ydb database get maximum-maxbot-db --format json)"; YS="$(printf %s "$YJ"|jget status)"; [ "$YS" = RUNNING ] && break; [ "$YS" = ERROR ] && die "YDB ERROR"; sleep 4; done
[ "${YS:-}" = RUNNING ] || die "YDB timeout"; YDB_CS="$(printf %s "$YJ"|jget endpoint)"; [ -n "$YDB_CS" ] || die "No YDB endpoint"

say "YMQ Standard queue + producer key"
yc iam access-key create --service-account-id "$ING_SA" --description maximum-maxbot-ingress-ymq --format json >"$TMP/key.json"
AWS_ACCESS_KEY_ID="$(cat "$TMP/key.json"|jget access_key.key_id)"; AWS_SECRET_ACCESS_KEY="$(cat "$TMP/key.json"|jget secret)"; KEY_ID="$(cat "$TMP/key.json"|jget access_key.id)"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" OUT="$TMP/q.json" "$VENV/bin/python" - <<'PYQ'
import boto3,json,os
s=boto3.client('sqs',endpoint_url='https://message-queue.api.cloud.yandex.net',region_name='ru-central1',aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'])
try:u=s.get_queue_url(QueueName='maximum-maxbot-events')['QueueUrl']
except Exception:u=s.create_queue(QueueName='maximum-maxbot-events',Attributes={'VisibilityTimeout':'60','MessageRetentionPeriod':'345600'})['QueueUrl']
a=s.get_queue_attributes(QueueUrl=u,AttributeNames=['QueueArn'])['Attributes']; json.dump({'url':u,'arn':a['QueueArn']},open(os.environ['OUT'],'w'))
PYQ
QURL="$(cat "$TMP/q.json"|jget url)"; QARN="$(cat "$TMP/q.json"|jget arn)"

ensure_c(){ local n="$1" j; j="$(getj yc serverless container get "$n")"; [ -n "$j" ] || { yc serverless container create --name "$n" --folder-id "$FOLDER_ID" >/dev/null; j="$(yc serverless container get "$n" --format json)"; }; printf %s "$j"; }
IJ="$(ensure_c maximum-maxbot-ingress)"; WJ="$(ensure_c maximum-maxbot-worker)"; IID="$(printf %s "$IJ"|jget id)"; WID="$(printf %s "$WJ"|jget id)"; IURL="$(printf %s "$IJ"|jget url)"; WURL="$(printf %s "$WJ"|jget url)"
[ -n "$IURL" ] && [ -n "$WURL" ] || die "Container URL missing"
yc serverless container deny-unauthenticated-invoke maximum-maxbot-worker >/dev/null 2>&1 || true
yc serverless container add-access-binding --name maximum-maxbot-worker --service-account-id "$TRG_SA" --role serverless-containers.containerInvoker >/dev/null

say "Deploy private worker; webhook remains OFF"
WE="APP_MODE=worker,MAX_BOT_TOKEN=$MAX_BOT_TOKEN,MAX_WEBHOOK_SECRET=$MAX_WEBHOOK_SECRET,MAX_ACTIVATE_WEBHOOK=0,YDB_CONNECTION_STRING=$YDB_CS"
yc serverless container revision deploy --container-name maximum-maxbot-worker --image "$IMG" --service-account-id "$WRK_SA" --runtime http --cores 1 --memory 256MB --execution-timeout 30s --concurrency 1 --min-instances 0 --environment "$WE" >"$TMP/deploy.log" 2>&1 || { tail -n 60 "$TMP/deploy.log" >&2; die "Worker deploy failed"; }

say "Deploy public ingress; webhook remains OFF"
IE="APP_MODE=ingress,MAX_BOT_TOKEN=$MAX_BOT_TOKEN,MAX_WEBHOOK_SECRET=$MAX_WEBHOOK_SECRET,MAX_ACTIVATE_WEBHOOK=0,PUBLIC_BASE_URL=$IURL,YMQ_ENDPOINT=https://message-queue.api.cloud.yandex.net,YMQ_QUEUE_URL=$QURL,YMQ_REGION=$REGION,AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
yc serverless container revision deploy --container-name maximum-maxbot-ingress --image "$IMG" --service-account-id "$ING_SA" --runtime http --cores 1 --memory 256MB --execution-timeout 10s --concurrency 4 --min-instances 0 --environment "$IE" >"$TMP/deploy.log" 2>&1 || { tail -n 60 "$TMP/deploy.log" >&2; die "Ingress deploy failed"; }
yc serverless container allow-unauthenticated-invoke maximum-maxbot-ingress >/dev/null

say "YMQ trigger -> private worker"
TJ="$(getj yc serverless trigger get maximum-maxbot-worker-trigger)"; [ -n "$TJ" ] || yc serverless trigger create message-queue --name maximum-maxbot-worker-trigger --queue "$QARN" --queue-service-account-id "$TRG_SA" --invoke-container-id "$WID" --invoke-container-service-account-id "$TRG_SA" --batch-size 1 --batch-cutoff 1s >/dev/null

say "Readiness and route isolation"
for _ in $(seq 1 40); do curl -fsS "$IURL/health" >"$TMP/ih.json" 2>/dev/null && break; sleep 2; done
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("mode")=="ingress"' "$TMP/ih.json"
[ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$IURL/trigger")" = 404 ] || die "Ingress route isolation failed"
curl -fsS "$IURL/ready" >"$TMP/ir.json"; python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("max_api") and d.get("queue") and d.get("activation_enabled") is False' "$TMP/ir.json"
IAM_TOKEN="$(yc iam create-token)"
for _ in $(seq 1 40); do C="$(curl -sS -o "$TMP/wh.json" -w '%{http_code}' -H "Authorization: Bearer $IAM_TOKEN" "$WURL/health" || true)"; [ "$C" = 200 ] && break; sleep 2; done
[ "${C:-}" = 200 ] || die "Private worker health failed"; [ "$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $IAM_TOKEN" "$WURL/webhook")" = 404 ] || die "Worker route isolation failed"
curl -fsS -H "Authorization: Bearer $IAM_TOKEN" "$WURL/ready" >"$TMP/wr.json"; python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d.get("ok") and d.get("max_api") and d.get("storage")' "$TMP/wr.json"; unset IAM_TOKEN

say "Exact YMQ -> worker -> YDB probe"
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" QURL="$QURL" OUT="$TMP/probe" "$VENV/bin/python" - <<'PYP'
import boto3,hashlib,json,os,time,uuid
u={'update_type':'maximum_bootstrap_probe','probe_id':str(uuid.uuid4()),'ts':int(time.time())}; c=json.dumps(u,ensure_ascii=False,sort_keys=True,separators=(',',':')); k='max:maximum_bootstrap_probe:sha256:'+hashlib.sha256(c.encode()).hexdigest()
s=boto3.client('sqs',endpoint_url='https://message-queue.api.cloud.yandex.net',region_name='ru-central1',aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']); s.send_message(QueueUrl=os.environ['QURL'],MessageBody=json.dumps(u,ensure_ascii=False)); open(os.environ['OUT'],'w').write(k)
PYP
PROBE="$(cat "$TMP/probe")"; OK=0
for _ in $(seq 1 75); do IAM_TOKEN="$(yc iam create-token)"; if YDB_CONNECTION_STRING="$YDB_CS" YDB_ACCESS_TOKEN_CREDENTIALS="$IAM_TOKEN" PROBE="$PROBE" "$VENV/bin/python" - <<'PYDB'
import os,ydb
d=ydb.Driver(connection_string=os.environ['YDB_CONNECTION_STRING'],credentials=ydb.credentials_from_env_variables())
try:
 d.wait(fail_fast=True,timeout=7); p=ydb.QuerySessionPool(d,size=1); r=p.execute_with_retries('DECLARE $id AS Utf8; SELECT event_id FROM processed_events WHERE event_id=$id LIMIT 1;',{'$id':os.environ['PROBE']},retry_settings=ydb.RetrySettings(max_retries=3,idempotent=True)); raise SystemExit(0 if r and r[0].rows else 3)
finally:d.stop(timeout=5)
PYDB
then OK=1; unset IAM_TOKEN; break; fi; unset IAM_TOKEN; sleep 4; done
[ "$OK" = 1 ] || die "Trigger probe not found in YDB within five minutes"

say "Remove superseded bootstrap-created YMQ producer keys"
L="$(yc iam access-key list --service-account-id "$ING_SA" --format json)"; OLD="$(L="$L" CUR="$KEY_ID" python3 -c 'import json,os;print("\\n".join(x.get("id","") for x in json.loads(os.environ["L"]) if x.get("id")!=os.environ["CUR"] and x.get("description")=="maximum-maxbot-ingress-ymq"))')"
while IFS= read -r x; do [ -n "$x" ] && yc iam access-key delete "$x" >/dev/null || true; done <<<"$OLD"
sudo docker logout cr.yandex >/dev/null 2>&1 || true

printf '\nYANDEX_INFRA_PREFLIGHT_OK\nIngress: %s\nWorker: private\nYDB: maximum-maxbot-db RUNNING\nQueue: maximum-maxbot-events Standard\nTrigger: OK\nMAX webhook activation: OFF\nOld MAX transport: NOT TOUCHED\n' "$IURL"
