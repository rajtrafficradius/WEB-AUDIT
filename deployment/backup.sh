#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_DIRECTORY:=/tmp/backups}"

mkdir -p "$BACKUP_DIRECTORY"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIRECTORY/seo-studio-$stamp.dump"
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL" --file="$target"
sha256sum "$target" > "$target.sha256"
printf '%s\n' "$target"

