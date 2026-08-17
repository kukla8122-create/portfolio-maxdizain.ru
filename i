#!/bin/bash
set -u
cd /root
URL="https://raw.githubusercontent.com/kukla8122-create/portfolio-maxdizain.ru/main/maxbot-final.py"
echo DOWNLOAD
rm -f newbot
if ! wget -q -O newbot "$URL"; then
  echo DOWNLOAD_FAILED
  exit 2
fi
if ! python3 -m py_compile newbot; then
  echo COMPILE_FAILED
  exit 3
fi
echo COMPILE_OK
cp -f maxbot.py maxbot.py.before_final
mv -f newbot maxbot.py
systemctl restart maxbot
sleep 5
if systemctl is-active --quiet maxbot && journalctl -u maxbot -n 30 --no-pager | grep -q "MAXBOT FINAL START"; then
  echo INSTALL_OK
  systemctl is-active maxbot
  journalctl -u maxbot -n 12 --no-pager
  echo FINAL_BOT_DONE
  exit 0
fi
echo NEW_BOT_FAILED
cp -f maxbot.py.before_final maxbot.py
systemctl restart maxbot
sleep 3
echo RESTORED_OLD_BOT
systemctl is-active maxbot
journalctl -u maxbot -n 12 --no-pager
exit 4
