#!/bin/bash
# Restore a journaled but unbroadcast batch. By default, restore equivalent
# rows with A's claim surviving and B's missing. RESTORE_MODE=postgres uses
# an actual pg_dump/pg_restore with neither claim present in the backup.
# A temporary fee rejection must not remove either coin from the retry queue.
. "$(dirname "$0")/lib.sh"
mkcfg
A=$(wname a); B=$(wname b); newwallet "$A"; newwallet "$B"
round 60000 "$A" "$B"
XA=$(coins "$A"); XB=$(coins "$B")
read -r PKA AMTA <<< "$(coininfo "$A" "$XA")"
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XA $XB" || finish
if [ "${RESTORE_MODE:-rows}" = postgres ]; then
	(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/backup.dump" || exit 2
fi
REJECTED=
TX=
RAW=
STOPPED=0
cleanup() {
	chmod u+w "$JOURNAL"
	[ -z "$REJECTED" ] || btc prioritisetransaction "$REJECTED" 0 100000 > /dev/null
	# A failing pre-fix run must not leave a restored claim blocking later cases.
	if [ -n "$TX" ] && [ -n "$RAW" ]; then
		q "UPDATE sidecar.payout SET state='signed', txid='$TX', raw_tx=decode('$RAW','hex')
			WHERE vtxo_id='$XA' AND state='claimed'" > /dev/null
		tick
		# A pre-fix full restore has no surviving row to reattach. Release
		# that fixture using its original transaction after assertions finish.
		[ "${RESTORE_MODE:-rows}" != postgres ] || btc sendrawtransaction "$RAW" > /dev/null 2>&1
	fi
	[ "$STOPPED" = 0 ] || (cd "$R" && docker compose start captaind watchmand > /dev/null)
}
trap cleanup EXIT
chmod a-w "$JOURNAL"
check "batch stored before journal write" pay_until "$XA $XB" 3 "'signed'"
chmod u+w "$JOURNAL"
TX=$(payout_txid "$XA")
RAW=$(q "SELECT encode(raw_tx,'hex') FROM sidecar.payout WHERE vtxo_id='$XA'")
check "both coins use one stored transaction" eq "$(payout_txid "$XB")" "$TX"
[ -n "$TX" ] || finish
btc prioritisetransaction "$TX" 0 -100000 > /dev/null
REJECTED=$TX
tick
check "both intents are durable" eq "$(journaled "$XA") $(journaled "$XB")" '1 1'
check "transaction still rejected" test -z "$(btc getmempoolentry "$TX" 2>/dev/null)"

if [ "${RESTORE_MODE:-rows}" = postgres ]; then
	STOPPED=1
	(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/backup.dump" > "$LOG/restore.log" 2>&1 || exit 2
	check "backup predates both claims" eq "$(payout_state "$XA")$(payout_state "$XB")" ""
else
	# Equivalent rows from a snapshot between the individual claim commits.
	q "UPDATE sidecar.payout SET state='claimed', txid=NULL, raw_tx=NULL WHERE vtxo_id='$XA';
		DELETE FROM sidecar.payout WHERE vtxo_id='$XB';
		UPDATE vtxo SET spend_state='spendable', banned_until_height=NULL, updated_at=NOW() WHERE vtxo_id='$XB';
		DELETE FROM sidecar.ban WHERE vtxo_id='$XB'" > /dev/null || exit 2
	eq "$(payout_state "$XA")|$(payout_txid "$XA")" 'claimed|' || exit 2
	eq "$(payout_state "$XB")|$(spend_state "$XB")" '|spendable' || exit 2
fi
tick
check "restored unclaimed member is excluded from Ark spending" eq "$(spend_state "$XB")" spent
if [ "${RESTORE_MODE:-rows}" = postgres ]; then
	check "both restored members are excluded before captaind restarts" eq "$(spend_state "$XA")" spent
	(cd "$R" && docker compose start captaind watchmand > /dev/null) || exit 2
	STOPPED=0
	check "captaind restarted after journal reconciliation" healthy captaind
else
	check "restored claim reattaches the original transaction" eq "$(payout_state "$XA")|$(payout_txid "$XA")" "signed|$TX"
fi
check "first recovery send still rejected" test -z "$(btc getmempoolentry "$TX" 2>/dev/null)"

btc prioritisetransaction "$TX" 0 100000 > /dev/null
REJECTED=
tick
check "later process retries the original journaled transaction" test -n "$(btc getmempoolentry "$TX" 2>/dev/null)"
check "A paid once" eq "$(wallet_sends_to "$(tr_address "$PKA")")" 1
check "B paid once despite its missing ledger row" eq "$(wallet_sends_to "$(tr_address "$PKB")")" 1
check "no replacement intent for B" eq "$(journaled "$XB")" 1
confirm_payouts
if [ "${RESTORE_MODE:-rows}" != postgres ]; then
	check "surviving ledger row confirmed" eq "$(payout_state "$XA")" confirmed
fi
finish
