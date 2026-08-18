#!/usr/bin/env bash
set -euo pipefail

# Safe, read-only preflight for the MAX bot Yandex Cloud deployment.
# It does not create, modify or delete cloud resources.

CLOUD_ID="b1g91dbs94slnmrj3npv"
FOLDER_ID="b1g7u7p1qmhjvgtidp0i"
EXPECTED_CLOUD_NAME="maximum-maxbot"

printf '\n=== MAX BOT / YANDEX PREFLIGHT ===\n'
printf 'UTC: '; date -u '+%Y-%m-%dT%H:%M:%SZ'

if ! command -v yc >/dev/null 2>&1; then
  echo 'FAIL: Yandex Cloud CLI (yc) is not available.'
  exit 10
fi

# This changes only the local Cloud Shell CLI context, not any cloud resource.
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

CLOUD_JSON="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FOLDER_JSON="$(yc resource-manager folder get "$FOLDER_ID" --format json)"

python3 - "$EXPECTED_CLOUD_NAME" <<'PY' <<<"$CLOUD_JSON"
import json, sys
expected = sys.argv[1]
d = json.load(sys.stdin)
name = d.get('name', '')
status = d.get('status', '')
cloud_id = d.get('id', '')
print(f'CLOUD name={name} id={cloud_id} status={status}')
if name != expected:
    print(f'FAIL: expected cloud name {expected!r}, got {name!r}')
    raise SystemExit(21)
if status != 'ACTIVE':
    print('WAIT: cloud is not ACTIVE yet. Do not deploy resources.')
    raise SystemExit(22)
PY

python3 - <<'PY' <<<"$FOLDER_JSON"
import json, sys
d = json.load(sys.stdin)
print(f"FOLDER name={d.get('name','')} id={d.get('id','')} status={d.get('status','')}")
status = d.get('status', '')
if status and status != 'ACTIVE':
    print('WAIT: folder is not ACTIVE yet. Do not deploy resources.')
    raise SystemExit(23)
PY

printf '\n=== TOOLS ===\n'
yc version || true
python3 --version || true
git --version || true
if command -v docker >/dev/null 2>&1; then
  docker --version || true
else
  echo 'docker=NOT_INSTALLED'
fi
if command -v terraform >/dev/null 2>&1; then
  terraform version | head -n 1 || true
else
  echo 'terraform=NOT_INSTALLED'
fi
if command -v aws >/dev/null 2>&1; then
  aws --version || true
else
  echo 'aws=NOT_INSTALLED'
fi

printf '\nPREFLIGHT_OK\n'
