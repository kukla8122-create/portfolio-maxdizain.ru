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

if ! command -v python3 >/dev/null 2>&1; then
  echo 'FAIL: python3 is not available.'
  exit 11
fi

# This changes only the local Cloud Shell CLI context, not any cloud resource.
yc config set cloud-id "$CLOUD_ID" >/dev/null
yc config set folder-id "$FOLDER_ID" >/dev/null

CLOUD_JSON="$(yc resource-manager cloud get "$CLOUD_ID" --format json)"
FOLDER_JSON="$(yc resource-manager folder get "$FOLDER_ID" --format json)"

# Current Resource Manager Cloud.Get no longer exposes a cloud status field.
# A successful get plus exact id/name verification is the read-only cloud check.
CLOUD_JSON="$CLOUD_JSON" python3 - "$EXPECTED_CLOUD_NAME" "$CLOUD_ID" <<'PY'
import json, os, sys
expected_name = sys.argv[1]
expected_id = sys.argv[2]
d = json.loads(os.environ['CLOUD_JSON'])
name = d.get('name', '')
cloud_id = d.get('id', '')
print(f'CLOUD name={name} id={cloud_id} status=NOT_EXPOSED_BY_API')
if name != expected_name:
    print(f'FAIL: expected cloud name {expected_name!r}, got {name!r}')
    raise SystemExit(21)
if cloud_id != expected_id:
    print(f'FAIL: expected cloud id {expected_id!r}, got {cloud_id!r}')
    raise SystemExit(22)
PY

FOLDER_JSON="$FOLDER_JSON" python3 - "$CLOUD_ID" "$FOLDER_ID" <<'PY'
import json, os, sys
expected_cloud_id = sys.argv[1]
expected_folder_id = sys.argv[2]
d = json.loads(os.environ['FOLDER_JSON'])
name = d.get('name', '')
folder_id = d.get('id', '')
cloud_id = d.get('cloud_id') or d.get('cloudId') or ''
status = d.get('status', '')
print(f'FOLDER name={name} id={folder_id} cloud_id={cloud_id} status={status}')
if folder_id != expected_folder_id:
    print(f'FAIL: expected folder id {expected_folder_id!r}, got {folder_id!r}')
    raise SystemExit(23)
if cloud_id != expected_cloud_id:
    print(f'FAIL: folder belongs to cloud {cloud_id!r}, expected {expected_cloud_id!r}')
    raise SystemExit(24)
if status != 'ACTIVE':
    print('WAIT: folder is not ACTIVE yet. Do not deploy resources.')
    raise SystemExit(25)
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
