#!/bin/sh
# Uses PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD (or PGPASSFILE).
# Run before captaind starts accepting requests; mount its sidecar journal read-only.
set -eu
if [ "${ABANDON_SHIP_SKIP_REASSERT_WAIT:-0}" = 1 ]; then
  echo 'WARNING: bypassing abandon-ship journal reassertion before captaind start' >&2
  exit 0
fi
: "${ABANDON_SHIP_JOURNAL:?set the path of the independently retained payout journal}"
if [ ! -f "$ABANDON_SHIP_JOURNAL" ] || [ ! -r "$ABANDON_SHIP_JOURNAL" ]; then
  echo 'Cannot check payout journal; restore/mount it before starting captaind' >&2
  exit 1
fi
sql() { psql -X -v ON_ERROR_STOP=1 -At -c "$1"; }
started=$(sql 'SELECT clock_timestamp()')

# A missing schema alone is not proof of a fresh deployment: a restored DB
# can predate the sidecar while the independent journal still has payments.
if [ ! -s "$ABANDON_SHIP_JOURNAL" ]; then
  empty=1
  for table in public.vtxo sidecar.payout; do
    if [ "$(sql "SELECT to_regclass('$table') IS NOT NULL")" = t ]; then
      [ "$(sql "SELECT EXISTS (SELECT 1 FROM $table LIMIT 1)")" = f ] || empty=0
    fi
  done
  if [ "$empty" = 1 ]; then
    echo 'Fresh empty database and payout journal; no settlements to reassert' >&2
    exit 0
  fi
fi

echo 'Waiting for abandon-ship to reassert its journal after this captaind start' >&2
while :; do
  if [ "$(sql "SELECT to_regclass('sidecar.reassert') IS NOT NULL")" = t ]; then
    if [ "$(sql "SELECT EXISTS (SELECT 1 FROM sidecar.reassert WHERE id=1 AND completed_at > '$started'::timestamptz)")" = t ]; then
      echo 'Abandon-ship journal reassertion complete; captaind may start' >&2
      exit 0
    fi
  fi
  sleep 1
done
