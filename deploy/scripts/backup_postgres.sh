#!/usr/bin/env bash
set -euo pipefail

backup_dir="/var/backups/jy-customer"
keep_days=30
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

: "${DATABASE_URL:?DATABASE_URL is required}"

install -d -m 0700 "$backup_dir"
umask 077
pg_dump --dbname="$DATABASE_URL" --format=custom --no-owner --file="$backup_dir/jy-customer-$timestamp.dump"
find "$backup_dir" -type f -name 'jy-customer-*.dump' -mtime +"$keep_days" -delete
