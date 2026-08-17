#!/bin/bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/kukla8122-create/portfolio-maxdizain.ru/main"
APP_DIR="/opt/maxbot"
SERVICE_FILE="/etc/systemd/system/maxbot-webhook.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root"
  exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

mkdir -p "$APP_DIR"

curl -fsSL "$REPO_RAW/maxbot-acceptance-v2.py" -o "$APP_DIR/maxbot-acceptance-v2.py"
curl -fsSL "$REPO_RAW/maxbot-webhook.py" -o "$APP_DIR/maxbot-webhook.py"
curl -fsSL "$REPO_RAW/deploy/maxbot-webhook.service" -o "$SERVICE_FILE"

chmod 700 "$APP_DIR/maxbot-acceptance-v2.py" "$APP_DIR/maxbot-webhook.py"
chmod 644 "$SERVICE_FILE"

# Existing bot credentials are intentionally reused; never overwrite them.
for secret_file in /root/.max_token /root/.giga_key; do
  if [ ! -s "$secret_file" ]; then
    echo "Missing required credential: $secret_file"
    exit 2
  fi
  chmod 600 "$secret_file"
done

if [ ! -s /root/.max_webhook_secret ]; then
  python3 - <<'PY' > /root/.max_webhook_secret
import secrets
print(secrets.token_hex(32))
PY
fi
chmod 600 /root/.max_webhook_secret

python3 -m py_compile "$APP_DIR/maxbot-acceptance-v2.py" "$APP_DIR/maxbot-webhook.py"

systemctl daemon-reload
systemctl enable maxbot-webhook.service
systemctl restart maxbot-webhook.service
sleep 1
systemctl --no-pager --full status maxbot-webhook.service || true

curl -fsS http://127.0.0.1:8787/health
echo
echo "Local webhook service is ready on 127.0.0.1:8787"
echo "Next: point bot.portfolio-maxdizain.ru to this VPS and configure FASTPANEL Reverse Proxy + Let's Encrypt."
