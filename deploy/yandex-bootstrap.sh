#!/usr/bin/env bash
set -euo pipefail
umask 077

# Canonical guarded launcher for «МАКСимум мебель» MAX bot on Yandex Cloud.
#
# The audited ordered-Data-Streams bootstrap is pinned to an immutable Git commit
# and Git blob below. Before execution we apply only three reviewed compatibility
# corrections discovered against the current Yandex Cloud documentation:
#   1) a Kinesis/Data Streams stream ID contains CLOUD_ID, not FOLDER_ID;
#   2) temporary DLQ configuration needs ymq.admin (SetQueueAttributes), while the
#      trigger runtime keeps only ymq.writer;
#   3) wait briefly for the temporary IAM binding to propagate before the SQS API.
#
# The pinned implementation itself validates Python/tests/Docker before cloud
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

for tool in curl git sed grep bash; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"
done

printf '\n=== MAX BOT / GUARDED YANDEX BOOTSTRAP ===\n'
printf 'Pinned implementation: %s\n' "$BASE_COMMIT"

curl -fsSL --retry 3 --proto '=https' --tlsv1.2 "$BASE_URL" -o "$BASE" 

ACTUAL_BLOB="$(git hash-object "$BASE")"
[ "$ACTUAL_BLOB" = "$BASE_BLOB" ] || die "Pinned bootstrap integrity check failed"

# Refuse to patch an unexpected implementation. Each source pattern must exist
# exactly where expected before the substitutions below are allowed.
[ "$(grep -Fc 'YDS_STREAM_ID="/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME"' "$BASE")" = 1 ] \
  || die "Unexpected Data Streams ID source pattern"
[ "$(grep -Fc 'grant_folder "$ING_SA" ymq.writer' "$BASE")" = 1 ] \
  || die "Unexpected temporary YMQ role source pattern"
[ "$(grep -Fc -- '--role ymq.writer --service-account-id "$ING_SA"' "$BASE")" = 2 ] \
  || die "Unexpected YMQ cleanup source pattern"
[ "$(grep -Fc 'TEMP_INGRESS_YMQ=1' "$BASE")" = 1 ] \
  || die "Unexpected IAM propagation source pattern"

sed \
  -e 's#YDS_STREAM_ID="/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME"#YDS_STREAM_ID="/$REGION/$CLOUD_ID/$YDB_ID/$STREAM_NAME"#' \
  -e 's#grant_folder "$ING_SA" ymq.writer#grant_folder "$ING_SA" ymq.admin#' \
  -e 's#--role ymq.writer --service-account-id "$ING_SA"#--role ymq.admin --service-account-id "$ING_SA"#g' \
  "$BASE" > "$PATCHED"

# IAM access-binding changes normally propagate quickly, but an immediate SQS call
# can race propagation. Five seconds keeps this first provisioning deterministic.
sed -i '/^TEMP_INGRESS_YMQ=1$/a sleep 5' "$PATCHED"

# Final invariants before any command from the implementation is executed.
[ "$(grep -Fc 'YDS_STREAM_ID="/$REGION/$CLOUD_ID/$YDB_ID/$STREAM_NAME"' "$PATCHED")" = 1 ] \
  || die "Reviewed Data Streams ID correction missing"
[ "$(grep -Fc 'grant_folder "$ING_SA" ymq.admin' "$PATCHED")" = 1 ] \
  || die "Reviewed temporary YMQ admin correction missing"
[ "$(grep -Fc -- '--role ymq.admin --service-account-id "$ING_SA"' "$PATCHED")" = 2 ] \
  || die "Reviewed YMQ cleanup correction missing"
[ "$(grep -Fc 'grant_folder "$TRG_SA" ymq.writer' "$PATCHED")" = 1 ] \
  || die "Trigger runtime ymq.writer role was unexpectedly changed"
[ "$(grep -Fc 'MAX webhook activation: OFF' "$PATCHED")" = 1 ] \
  || die "MAX webhook OFF invariant missing"

bash -n "$PATCHED" || die "Reviewed bootstrap shell syntax check failed"
printf 'Integrity: OK\nCompatibility corrections: OK\nShell syntax: OK\n'
printf 'MAX webhook activation: OFF throughout bootstrap\n\n'

exec bash "$PATCHED"
