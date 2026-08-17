#!/bin/bash
set -u

CLIENT_IP="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root"
  exit 1
fi

if ! printf '%s' "$CLIENT_IP" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
  echo "ERROR: valid IPv4 required"
  exit 2
fi

echo "=== RESCUE START ==="
echo "Client IP: $CLIENT_IP"

STAMP="$(date +%Y%m%d-%H%M%S)"
if command -v iptables-save >/dev/null 2>&1; then
  iptables-save > "/root/iptables-before-rescue-$STAMP.rules" || true
fi

# Put a narrow temporary allow rule first so an old FASTPANEL/firewall rule
# or Fail2ban ban cannot block the current administrator IP.
if command -v iptables >/dev/null 2>&1; then
  if ! iptables -C INPUT -s "$CLIENT_IP" -p tcp -m multiport --dports 22,8888 -j ACCEPT 2>/dev/null; then
    iptables -I INPUT 1 -s "$CLIENT_IP" -p tcp -m multiport --dports 22,8888 -j ACCEPT
    echo "Firewall: temporary allow rule added for $CLIENT_IP on 22,8888"
  else
    echo "Firewall: allow rule already present"
  fi
fi

# Remove this IP from every active Fail2ban jail, if Fail2ban is installed.
if command -v fail2ban-client >/dev/null 2>&1; then
  JAILS="$(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' ')"
  for JAIL in $JAILS; do
    fail2ban-client set "$JAIL" unbanip "$CLIENT_IP" >/dev/null 2>&1 || true
  done
  echo "Fail2ban: unban attempted in all active jails"
fi

# Never restart SSH with an invalid config.
if command -v sshd >/dev/null 2>&1 && sshd -t; then
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
  echo "SSH: configuration valid; service restart requested"
else
  echo "SSH: configuration check failed or sshd not found; not restarted"
fi

# Restart only FASTPANEL/web services that actually exist on this server.
for SERVICE in fastpanel2-nginx fastpanel2 nginx; do
  if systemctl list-unit-files "$SERVICE.service" --no-legend 2>/dev/null | grep -q "$SERVICE.service"; then
    systemctl restart "$SERVICE.service" 2>/dev/null || true
    echo "$SERVICE: restart requested"
  fi
done

echo "=== LISTENERS ==="
ss -lntp 2>/dev/null | grep -E ':22 |:8888 ' || true

echo "=== LOCAL FASTPANEL HTTPS ==="
curl -vk --connect-timeout 5 --max-time 10 https://127.0.0.1:8888/ -o /dev/null 2>&1 | tail -n 15 || true

echo "=== SYSTEM ==="
uptime || true
free -m || true
df -h / || true

echo "=== RESCUE_DONE ==="