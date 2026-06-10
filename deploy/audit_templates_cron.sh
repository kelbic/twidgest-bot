#!/usr/bin/env bash
# Месячный аудит источников шаблонов + алерт админу в Telegram.
# Запускается из twidgest-template-audit.timer.
set -uo pipefail
cd /root/twidgest-bot

set -a; source .env; set +a

OUT=$(venv/bin/python tools/audit_templates.py 2>&1)
RC=$?

if [ $RC -ne 0 ]; then
  TEXT="❌ Аудит шаблонов УПАЛ (rc=$RC):
$(echo "$OUT" | tail -10)"
else
  SUMMARY=$(echo "$OUT" | awk '/^ИТОГО:/{f=1} f' | head -12)
  WARN=$(echo "$SUMMARY" | grep -c "⚠️\|❌" || true)
  HEAD="📋 Месячный аудит шаблонов"
  [ "$WARN" -gt 0 ] && HEAD="⚠️ Аудит шаблонов: есть проблемы ($WARN)"
  TEXT="$HEAD
$SUMMARY"
fi

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${ADMIN_USER_ID}" \
  --data-urlencode text="$TEXT" > /dev/null
echo "audit done, rc=$RC, alert sent"
