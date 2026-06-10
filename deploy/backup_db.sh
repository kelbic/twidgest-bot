#!/usr/bin/env bash
# Ночной бэкап SQLite-баз: онлайн-снапшот через .backup (WAL-safe),
# gzip, ротация 14 суток. Запускается из twidgest-backup.timer.
set -euo pipefail

BACKUP_DIR="/root/backups/twidgest"
STAMP="$(date +%Y%m%d-%H%M)"
KEEP_DAYS=14

# (путь к базе, префикс имени копии)
DBS=(
  "/root/twidgest-bot/twidgest.db twidgest"
  "/root/essayist-bot/essayist.db essayist"
)

mkdir -p "$BACKUP_DIR"

for entry in "${DBS[@]}"; do
  db_path="${entry% *}"
  name="${entry##* }"
  if [ ! -f "$db_path" ]; then
    echo "skip: $db_path не существует"
    continue
  fi
  out="$BACKUP_DIR/${name}-${STAMP}.db"
  sqlite3 "$db_path" ".backup '$out'"
  gzip -f "$out"
  echo "ok: ${out}.gz ($(du -h "${out}.gz" | cut -f1))"
done

# Ротация
find "$BACKUP_DIR" -name "*.db.gz" -mtime +"$KEEP_DAYS" -delete
echo "rotation: оставлены копии за последние $KEEP_DAYS суток"
