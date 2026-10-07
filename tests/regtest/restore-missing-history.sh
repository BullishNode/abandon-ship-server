#!/bin/bash
# An older captaind backup cannot replace Ark history absent from the journal.
# Restore actual dumps, retain the payment journal, and require an explicit stop.
. "$(dirname "$0")/lib.sh"
mkcfg
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/before.dump" || exit 2
expired_coin 60000
check "real payment exists before the restore" pay_until "$X"
TX=$(payout_txid "$X")
[ -n "$TX" ] || finish
confirm_payouts
RAW=$(journal_raw "$X")
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/current.dump" || exit 2
STOPPED=0
restore_current() {
	[ "$STOPPED" = 1 ] || return 0
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/current.dump" > "$LOG/cleanup.log" 2>&1 || return 1
	(cd "$R" && docker compose start captaind watchmand > /dev/null) || return 1
	STOPPED=0
	healthy captaind || return 1
}
trap 'restore_current || echo "SETUP FAIL: restore current.dump before restarting captaind" >&2' EXIT
STOPPED=1
(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/before.dump" > "$LOG/restore.log" 2>&1 || exit 2
check "backup predates the settled Ark coin" eq "$(q "SELECT count(*) FROM vtxo WHERE vtxo_id='$X'")" 0
RC=0; tick || RC=$?
check "startup stops on missing Ark history" test "$RC" -ne 0
check "operator gets the required recovery action" grep -q 'restore a database backup and WAL containing its Ark history' "$LOG/tick.log"
check "original payment bytes remain recoverable" eq "$(journal_raw "$X")" "$RAW"
check "original payment remains on-chain" test -n "$(btc getrawtransaction "$TX" 1)"
restore_current || exit 2
finish
