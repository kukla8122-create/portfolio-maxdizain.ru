#!/usr/bin/env bash
set -uo pipefail
umask 077

# Read-only MAX bot token diagnostic.
# It performs only GET /me against the current official MAX API domain.
# It NEVER creates/updates/deletes subscriptions and NEVER mutates Yandex Cloud.

MAX_API="https://platform-api2.max.ru"
TMP="$(mktemp -d)"
MAX_TOKEN=""
cleanup(){
  unset MAX_TOKEN || true
  rm -rf "$TMP"
}
trap cleanup EXIT

die(){ printf 'MAX_TOKEN_CHECK=LOCAL_ERROR detail=%s\n' "$1" >&2; exit 2; }
for tool in curl python3 openssl; do command -v "$tool" >/dev/null 2>&1 || die "missing_$tool"; done
[ -r /dev/tty ] || die "tty_unavailable"

printf '=== MAX BOT / READ-ONLY TOKEN CHECK ===\n'
printf 'Only GET /me is called. Webhook/subscriptions are not changed.\n'

# MAX requires trust in the Russian Trusted Root CA. Use one reviewed RSA root
# certificate only; do not concatenate the current multi-certificate government
# bundles into curl --cacert because unsupported certificates in such a bundle can
# make libcurl/OpenSSL reject the whole CA file with curl error 77.
MAX_CA="$TMP/russian-trusted-root-ca.pem"
cat >"$MAX_CA" <<'PEM'
-----BEGIN CERTIFICATE-----
MIIFwjCCA6qgAwIBAgICEAAwDQYJKoZIhvcNAQELBQAwcDELMAkGA1UEBhMCUlUx
PzA9BgNVBAoMNlRoZSBNaW5pc3RyeSBvZiBEaWdpdGFsIERldmVsb3BtZW50IGFu
ZCBDb21tdW5pY2F0aW9uczEgMB4GA1UEAwwXUnVzc2lhbiBUcnVzdGVkIFJvb3Qg
Q0EwHhcNMjIwMzAxMjEwNDE1WhcNMzIwMjI3MjEwNDE1WjBwMQswCQYDVQQGEwJS
VTE/MD0GA1UECgw2VGhlIE1pbmlzdHJ5IG9mIERpZ2l0YWwgRGV2ZWxvcG1lbnQg
YW5kIENvbW11bmljYXRpb25zMSAwHgYDVQQDDBdSdXNzaWFuIFRydXN0ZWQgUm9v
dCBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAMfFOZ8pUAL3+r2n
qqE0Zp52selXsKGFYoG0GM5bwz1bSFtCt+AZQMhkWQheI3poZAToYJu69pHLKS6Q
XBiwBC1cvzYmUYKMYZC7jE5YhEU2bSL0mX7NaMxMDmH2/NwuOVRj8OImVa5s1F4U
zn4Kv3PFlDBjjSjXKVY9kmjUBsXQrIHeaqmUIsPIlNWUnimXS0I0abExqkbdrXbX
YwCOXhOO2pDUx3ckmJlCMUGacUTnylyQW2VsJIyIGA8V0xzdaeUXg0VZ6ZmNUr5Y
Ber/EAOLPb8NYpsAhJe2mXjMB/J9HNsoFMBFJ0lLOT/+dQvjbdRZoOT8eqJpWnVD
U+QL/qEZnz57N88OWM3rabJkRNdU/Z7x5SFIM9FrqtN8xewsiBWBI0K6XFuOBOTD
4V08o4TzJ8+Ccq5XlCUW2L48pZNCYuBDfBh7FxkB7qDgGDiaftEkZZfApRg2E+M9
G8wkNKTPLDc4wH0FDTijhgxR3Y4PiS1HL2Zhw7bD3CbslmEGgfnnZojNkJtcLeBH
BLa52/dSwNU4WWLubaYSiAmA9IUMX1/RpfpxOxd4Ykmhz97oFbUaDJFipIggx5sX
ePAlkTdWnv+RWBxlJwMQ25oEHmRguNYf4Zr/Rxr9cS93Y+mdXIZaBEE0KS2iLRqa
OiWBki9IMQU4phqPOBAaG7A+eP8PAgMBAAGjZjBkMB0GA1UdDgQWBBTh0YHlzlpf
BKrS6badZrHF+qwshzAfBgNVHSMEGDAWgBTh0YHlzlpfBKrS6badZrHF+qwshzAS
BgNVHRMBAf8ECDAGAQH/AgEEMA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsF
AAOCAgEAALIY1wkilt/urfEVM5vKzr6utOeDWCUczmWX/RX4ljpRdgF+5fAIS4vH
tmXkqpSCOVeWUrJV9QvZn6L227ZwuE15cWi8DCDal3Ue90WgAJJZMfTshN4OI8cq
W9E4EG9wglbEtMnObHlms8F3CHmrw3k6KmUkWGoa+/ENmcVl68u/cMRl1JbW2bM+
/3A+SAg2c6iPDlehczKx2oa95QW0SkPPWGuNA/CE8CpyANIhu9XFrj3RQ3EqeRcS
AQQod1RNuHpfETLU/A2gMmvn/w/sx7TB3W5BPs6rprOA37tutPq9u6FTZOcG1Oqj
C/B7yTqgI7rbyvox7DEXoX7rIiEqyNNUguTk/u3SZ4VXE2kmxdmSh3TQvybfbnXV
4JbCZVaqiZraqc7oZMnRoWrXRG3ztbnbes/9qhRGI7PqXqeKJBztxRTEVj8ONs1d
WN5szTwaPIvhkhO3CO5ErU2rVdUr89wKpNXbBODFKRtgxUT70YpmJ46VVaqdAhOZ
D9EUUn4YaeLaS8AjSF/h7UkjOibNc4qVDiPP+rkehFWM66PVnP1Msh93tc+taIfC
EYVMxjh8zNbFuoc7fzvvrFILLe7ifvEIUqSVIC/AzplM/Jxw7buXFeGP1qVCBEHq
391d/9RAfaZ12zkwFsl+IKwE/OZxW8AHa9i1p4GO0YSNuczzEm4=
-----END CERTIFICATE-----
PEM
openssl x509 -in "$MAX_CA" -noout -subject -issuer -dates >/dev/null 2>&1 \
  || { printf 'MAX_TOKEN_CHECK=CA_PARSE_ERROR\n'; exit 3; }
openssl verify -CAfile "$MAX_CA" "$MAX_CA" >/dev/null 2>&1 \
  || { printf 'MAX_TOKEN_CHECK=CA_VERIFY_ERROR\n'; exit 3; }
printf 'MAX_CA_CHECK=OK\n'

printf 'Paste NEW MAX_BOT_TOKEN here (input is hidden): '
IFS= read -r -s MAX_TOKEN </dev/tty
printf '\n'
[ -n "$MAX_TOKEN" ] || { printf 'MAX_TOKEN_CHECK=EMPTY\n'; exit 4; }

BODY="$TMP/me.json"
ERR="$TMP/curl.err"
set +e
HTTP_CODE="$(curl --cacert "$MAX_CA" --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  --output "$BODY" --write-out '%{http_code}' \
  -X GET "$MAX_API/me" -H "Authorization: $MAX_TOKEN" 2>"$ERR")"
CURL_RC=$?
set -e

if [ "$CURL_RC" -ne 0 ]; then
  printf 'MAX_TOKEN_CHECK=TRANSPORT_ERROR curl_exit=%s http=%s\n' "$CURL_RC" "${HTTP_CODE:-000}"
  sed -n '1,3p' "$ERR" | sed 's/^/transport_detail=/'
  exit 5
fi

case "$HTTP_CODE" in
  200)
    if python3 - "$BODY" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    d=json.load(f)
if d.get('is_bot') is not True:
    raise SystemExit(1)
print('MAX_TOKEN_CHECK=VALID')
print('bot_user_id='+str(d.get('user_id','')))
print('bot_username='+str(d.get('username') or ''))
print('bot_name='+str(d.get('first_name') or d.get('name') or ''))
PY
    then exit 0; else printf 'MAX_TOKEN_CHECK=BAD_200_RESPONSE\n'; exit 6; fi
    ;;
  401) printf 'MAX_TOKEN_CHECK=INVALID_HTTP_401\n'; exit 7 ;;
  429) printf 'MAX_TOKEN_CHECK=RATE_LIMIT_HTTP_429\n'; exit 8 ;;
  500|502|503|504) printf 'MAX_TOKEN_CHECK=MAX_SERVER_HTTP_%s\n' "$HTTP_CODE"; exit 9 ;;
  *) printf 'MAX_TOKEN_CHECK=UNEXPECTED_HTTP_%s\n' "${HTTP_CODE:-000}"; exit 10 ;;
esac
