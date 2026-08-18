#!/usr/bin/env bash
set -euo pipefail
umask 077

# Canonical guarded launcher for «МАКСимум мебель» MAX bot.
#
# The full Cloud Functions implementation is pinned to an immutable Git commit and
# Git blob. This launcher verifies the exact source, applies only two reviewed
# compatibility hardenings, validates shell syntax, and only then executes it:
#   1) use the current explicit Cloud Functions byteSize spelling 256MB;
#   2) parameterize the final YDB end-to-end lookup instead of interpolating a key.
#
# The pinned implementation itself runs Python/unit tests before cloud mutations,
# does not use Docker/Container Registry/Lockbox, and NEVER activates MAX webhook.

REPOSITORY="kukla8122-create/portfolio-maxdizain.ru"
BASE_COMMIT="9a5ea38136d9fecddd59cd70c7e7ce1b11c3a84e"
BASE_BLOB="da2f56eaee11955ee3b9b7a99dfb9b41ca17015c"
BASE_URL="https://raw.githubusercontent.com/${REPOSITORY}/${BASE_COMMIT}/deploy/yandex-bootstrap.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BASE="$TMP/yandex-bootstrap.base.sh"
PATCHED="$TMP/yandex-bootstrap.reviewed.sh"

die(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
for tool in curl git python3 grep bash; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing tool: $tool"
done

printf '\n=== MAX BOT / GUARDED CLOUD FUNCTIONS BOOTSTRAP ===\n'
printf 'Pinned implementation: %s\n' "$BASE_COMMIT"
curl -fsSL --retry 3 --proto '=https' --tlsv1.2 "$BASE_URL" -o "$BASE"
ACTUAL_BLOB="$(git hash-object "$BASE")"
[ "$ACTUAL_BLOB" = "$BASE_BLOB" ] || die "Pinned bootstrap integrity check failed"

BASE="$BASE" PATCHED="$PATCHED" python3 - <<'PY'
from pathlib import Path
import os

base = Path(os.environ["BASE"])
patched = Path(os.environ["PATCHED"])
text = base.read_text(encoding="utf-8")

# Current CLI documents byteSize values such as 128MB/1GB. The historical 256m
# spelling also works in examples, but use the explicit current spelling here.
if text.count("--memory 256m") != 2:
    raise SystemExit("Unexpected Cloud Functions memory source pattern")
text = text.replace("--memory 256m", "--memory 256MB")

old = '''FOUND=0
for _ in $(seq 1 180); do
  IAM_TOKEN="$(yc iam create-token)"; export IAM_TOKEN
  if ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" sql \\
    -s "SELECT event_id FROM processed_events WHERE event_id = '$EXPECTED_KEY';" \\
    --format json-unicode >"$TMP/e2e.txt" 2>/dev/null && grep -Fq "$EXPECTED_KEY" "$TMP/e2e.txt"; then
    FOUND=1
    break
  fi
  unset IAM_TOKEN
  sleep 2
done
unset IAM_TOKEN'''

new = '''cat >"$TMP/e2e.sql" <<'SQL'
DECLARE $event_id AS Utf8;
SELECT event_id FROM processed_events WHERE event_id=$event_id;
SQL
EXPECTED_KEY="$EXPECTED_KEY" python3 - <<'PY2' >"$TMP/e2e.json"
import json, os
print(json.dumps({"event_id": os.environ["EXPECTED_KEY"]}))
PY2
FOUND=0
for _ in $(seq 1 180); do
  IAM_TOKEN="$(yc iam create-token)"; export IAM_TOKEN
  if ydb --endpoint "$YDB_GRPC" --database "$YDB_PATH" sql \\
    -f "$TMP/e2e.sql" --input-file "$TMP/e2e.json" \\
    --format json-unicode >"$TMP/e2e.txt" 2>/dev/null && grep -Fq "$EXPECTED_KEY" "$TMP/e2e.txt"; then
    FOUND=1
    unset IAM_TOKEN
    break
  fi
  unset IAM_TOKEN
  sleep 2
done
unset IAM_TOKEN'''

if text.count(old) != 1:
    raise SystemExit("Unexpected end-to-end YDB lookup source pattern")
text = text.replace(old, new)
patched.write_text(text, encoding="utf-8")
PY

# Refuse execution if any reviewed invariant is missing.
[ "$(grep -Fc -- '--memory 256MB' "$PATCHED")" = 2 ] \
  || die "Cloud Functions memory hardening missing"
[ "$(grep -Fc 'DECLARE $event_id AS Utf8;' "$PATCHED")" = 1 ] \
  || die "Parameterized E2E query missing"
[ "$(grep -Fc -- '--input-file "$TMP/e2e.json"' "$PATCHED")" = 1 ] \
  || die "Parameterized E2E input file missing"
[ "$(grep -Fc "WHERE event_id = '$EXPECTED_KEY'" "$PATCHED")" = 0 ] \
  || die "Unsafe E2E interpolation unexpectedly remains"
[ "$(grep -Fci 'yc container registry' "$PATCHED")" = 0 ] \
  || die "Container Registry dependency unexpectedly present"
[ "$(grep -Fci 'yc lockbox' "$PATCHED")" = 0 ] \
  || die "Lockbox dependency unexpectedly present"
[ "$(grep -Fc 'MAX webhook activation: OFF' "$PATCHED")" = 1 ] \
  || die "MAX webhook OFF invariant missing"

bash -n "$PATCHED" || die "Reviewed bootstrap shell syntax check failed"
printf 'Integrity: OK\n'
printf 'Cloud Functions CLI compatibility: OK\n'
printf 'Parameterized YDB verification: OK\n'
printf 'Shell syntax: OK\n'
printf 'MAX webhook activation: OFF throughout bootstrap\n\n'

exec bash "$PATCHED"
