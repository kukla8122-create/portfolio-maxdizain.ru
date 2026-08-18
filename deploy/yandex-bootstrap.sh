#!/usr/bin/env bash
set -euo pipefail
umask 077

# Canonical guarded launcher for «МАКСимум мебель» MAX bot on Yandex Cloud.
#
# The audited ordered-Data-Streams bootstrap is pinned to an immutable Git commit
# and Git blob below. Before execution we apply only reviewed compatibility
# corrections discovered against current Yandex Cloud / Buildah behavior:
#   1) a Kinesis/Data Streams stream ID contains CLOUD_ID, not FOLDER_ID;
#   2) temporary DLQ configuration needs ymq.admin (SetQueueAttributes), while the
#      trigger runtime keeps only ymq.writer;
#   3) wait briefly for the temporary IAM binding to propagate before the SQS API;
#   4) Yandex Cloud Shell is treated as a daemonless build environment: use
#      Buildah with vfs storage + chroot isolation instead of starting dockerd.
#
# The pinned implementation itself validates Python/tests/image build before cloud
# provisioning and NEVER activates, replaces or deletes a MAX webhook.

REPOSITORY="kukla8122-create/portfolio-maxdizain.ru"
BASE_COMMIT="751aa5765e570f05762df416043c6d374f7c4441"
BASE_BLOB="e06f0829dd43ec66d86d17b836e83c207475a708"
BASE_URL="https://raw.githubusercontent.com/${REPOSITORY}/${BASE_COMMIT}/deploy/yandex-bootstrap.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE="$TMP/yandex-bootstrap.base.sh"
PATCHED="$TMP/yandex-bootstrap.reviewed.sh"

die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

for tool in curl git grep bash python3; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"
done

printf '\n=== MAX BOT / GUARDED YANDEX BOOTSTRAP ===\n'
printf 'Pinned implementation: %s\n' "$BASE_COMMIT"

curl -fsSL --retry 3 --proto '=https' --tlsv1.2 "$BASE_URL" -o "$BASE"

ACTUAL_BLOB="$(git hash-object "$BASE")"
[ "$ACTUAL_BLOB" = "$BASE_BLOB" ] || die "Pinned bootstrap integrity check failed"

# Refuse to transform an unexpected implementation. Each exact source fragment
# below must occur once before any substitution is allowed.
BASE="$BASE" PATCHED="$PATCHED" python3 - <<'PY'
from pathlib import Path
import os

base = Path(os.environ["BASE"])
patched = Path(os.environ["PATCHED"])
text = base.read_text(encoding="utf-8")

replacements = [
    (
        'YDS_STREAM_ID="/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME"',
        'YDS_STREAM_ID="/$REGION/$CLOUD_ID/$YDB_ID/$STREAM_NAME"',
        "Data Streams ID",
    ),
    (
        'grant_folder "$ING_SA" ymq.writer',
        'grant_folder "$ING_SA" ymq.admin',
        "temporary YMQ role",
    ),
    (
        '--role ymq.writer --service-account-id "$ING_SA"',
        '--role ymq.admin --service-account-id "$ING_SA"',
        "temporary YMQ cleanup role",
    ),
    (
        'sudo docker logout cr.yandex >/dev/null 2>&1 || true',
        'sudo buildah logout cr.yandex >/dev/null 2>&1 || true',
        "registry logout",
    ),
]

for old, new, label in replacements:
    count = text.count(old)
    expected = 2 if label == "temporary YMQ cleanup role" else 1
    if count != expected:
        raise SystemExit(f"Unexpected {label} source pattern count: {count}, expected {expected}")
    text = text.replace(old, new)

old_prepare = '''say "Prepare Cloud Shell build tools"
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
'''
new_prepare = '''say "Prepare daemonless Cloud Shell image builder"
if ! command -v buildah >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq buildah
fi
command -v buildah >/dev/null 2>&1 || die "Buildah installation failed"
sudo buildah --storage-driver vfs info >/dev/null 2>&1 \
  || die "Buildah vfs storage is unavailable in Cloud Shell"
printf 'Buildah: %s\\n' "$(buildah --version)"
'''
if text.count(old_prepare) != 1:
    raise SystemExit("Unexpected Docker preparation block")
text = text.replace(old_prepare, new_prepare)

old_build = '''sudo docker build --pull -f Dockerfile.yandex -t maxbot-yandex:bootstrap . >"$TMP/build.log" 2>&1 \\
  || { tail -n 100 "$TMP/build.log" >&2; die "Docker build failed"; }
say "Source tests and Docker build passed before provisioning"'''
new_build = '''sudo buildah --storage-driver vfs build --pull=always --isolation chroot --format docker \\
  -f Dockerfile.yandex -t maxbot-yandex:bootstrap . >"$TMP/build.log" 2>&1 \\
  || { tail -n 120 "$TMP/build.log" >&2; die "Buildah image build failed"; }
say "Source tests and daemonless image build passed before provisioning"'''
if text.count(old_build) != 1:
    raise SystemExit("Unexpected Docker build block")
text = text.replace(old_build, new_build)

old_push = '''yc iam create-token | sudo docker login --username iam --password-stdin cr.yandex >/dev/null 2>&1
sudo docker tag maxbot-yandex:bootstrap "$IMG"
sudo docker push "$IMG" >"$TMP/push.log" 2>&1 \\
  || { tail -n 100 "$TMP/push.log" >&2; die "Docker push failed"; }'''
new_push = '''yc iam create-token | sudo buildah login --username iam --password-stdin cr.yandex >/dev/null 2>&1 \\
  || die "Container Registry login failed"
sudo buildah --storage-driver vfs tag maxbot-yandex:bootstrap "$IMG"
sudo buildah --storage-driver vfs push "$IMG" "docker://$IMG" >"$TMP/push.log" 2>&1 \\
  || { tail -n 120 "$TMP/push.log" >&2; die "Buildah push failed"; }'''
if text.count(old_push) != 1:
    raise SystemExit("Unexpected Docker registry push block")
text = text.replace(old_push, new_push)

# IAM access-binding changes normally propagate quickly, but an immediate SQS call
# can race propagation. Add a short deterministic wait exactly once.
needle = "TEMP_INGRESS_YMQ=1\n"
if text.count(needle) != 1:
    raise SystemExit("Unexpected IAM propagation source pattern")
text = text.replace(needle, needle + "sleep 5\n", 1)

patched.write_text(text, encoding="utf-8")
PY

# Final invariants before any command from the implementation is executed.
[ "$(grep -Fc 'YDS_STREAM_ID="/$REGION/$CLOUD_ID/$YDB_ID/$STREAM_NAME"' "$PATCHED")" = 1 ] \
  || die "Reviewed Data Streams ID correction missing"
[ "$(grep -Fc 'grant_folder "$ING_SA" ymq.admin' "$PATCHED")" = 1 ] \
  || die "Reviewed temporary YMQ admin correction missing"
[ "$(grep -Fc -- '--role ymq.admin --service-account-id "$ING_SA"' "$PATCHED")" = 2 ] \
  || die "Reviewed YMQ cleanup correction missing"
[ "$(grep -Fc 'grant_folder "$TRG_SA" ymq.writer' "$PATCHED")" = 1 ] \
  || die "Trigger runtime ymq.writer role was unexpectedly changed"
[ "$(grep -Fc 'sudo buildah --storage-driver vfs build --pull=always --isolation chroot --format docker' "$PATCHED")" = 1 ] \
  || die "Reviewed daemonless Buildah build correction missing"
[ "$(grep -Fc 'sudo buildah --storage-driver vfs push "$IMG" "docker://$IMG"' "$PATCHED")" = 1 ] \
  || die "Reviewed Buildah registry push correction missing"
[ "$(grep -Fc 'Docker daemon unavailable' "$PATCHED")" = 0 ] \
  || die "Docker daemon dependency unexpectedly remains"
[ "$(grep -Fc 'dockerd' "$PATCHED")" = 0 ] \
  || die "dockerd dependency unexpectedly remains"
[ "$(grep -Fc 'MAX webhook activation: OFF' "$PATCHED")" = 1 ] \
  || die "MAX webhook OFF invariant missing"

bash -n "$PATCHED" || die "Reviewed bootstrap shell syntax check failed"
printf 'Integrity: OK\nCompatibility corrections: OK\nDaemonless Buildah path: OK\nShell syntax: OK\n'
printf 'MAX webhook activation: OFF throughout bootstrap\n\n'

if [ "${MAX_BOOTSTRAP_VALIDATE_ONLY:-0}" = "1" ]; then
  printf 'BOOTSTRAP_TRANSFORM_VALIDATION_OK\n'
  exit 0
fi

exec bash "$PATCHED"
