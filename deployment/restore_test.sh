#!/bin/sh
set -eu

: "${RESTORE_TEST_DATABASE_URL:?RESTORE_TEST_DATABASE_URL is required}"
: "${1:?Pass the pg_dump file to restore}"

dump="$1"
sha256sum --check "$dump.sha256"
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$RESTORE_TEST_DATABASE_URL" "$dump"
DATABASE_URL="$RESTORE_TEST_DATABASE_URL" python manage.py migrate --check
DATABASE_URL="$RESTORE_TEST_DATABASE_URL" python manage.py check --deploy

