#!/bin/bash
# Actual SIGKILL after journal fsync, before sendrawtransaction reaches Core.
# Restore a real post-claim dump; verify atomic batch reattachment and repeat
# the restore after confirmation. The independent journal is never restored.
. "$(dirname "$0")/lib.sh"
mkcfg
A=$(wname batch-a); B=$(wname batch-b); newwallet "$A"; newwallet "$B"
round 60000 "$A" "$B"
XA=$(coins "$A"); XB=$(coins "$B")
read -r PKA AMTA <<< "$(coininfo "$A" "$XA")"
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
expire_and_sweep "$XA $XB" || finish
lock_wallet
PROXY=; SIDECAR=; STOPPED=0; LOCKER=
cleanup() {
	[ -z "$SIDECAR" ] || { kill "$SIDECAR" 2>/dev/null; wait "$SIDECAR" 2>/dev/null; }
	[ -z "$PROXY" ] || { kill "$PROXY" 2>/dev/null; wait "$PROXY" 2>/dev/null; }
	[ -z "$LOCKER" ] || wait "$LOCKER" 2>/dev/null
	unlock_wallet
	if [ "$STOPPED" = 1 ]; then
		# A failed pre-fix assertion must still leave the original payment
		# recoverable before this fixture restarts captaind.
		cp "$LOG/direct.toml" "$CFG"
		tick || return 1
		(cd "$R" && docker compose start captaind watchmand > /dev/null)
	fi
}
trap cleanup EXIT
check "both coins claimed without signing" pay_until "$XA $XB" 3 "'claimed'"
eq "$(payout_state "$XA")|$(payout_state "$XB")|$(payout_txid "$XA")$(payout_txid "$XB")" 'claimed|claimed|' || exit 2
(cd "$R" && docker compose exec -T postgres pg_dump -U postgres -Fc bark-server-db) > "$LOG/claimed.dump" || exit 2
unlock_wallet
cp "$CFG" "$LOG/direct.toml"
python3 "$(dirname "$0")/rpc-hold.py" "$CFG" "$LOG" > "$LOG/proxy.log" 2>&1 & PROXY=$!
for i in $(seq 60); do [ -s "$LOG/port.txt" ] && break; kill -0 "$PROXY" 2>/dev/null || exit 2; sleep 1; done
[ -s "$LOG/port.txt" ] || exit 2
sed -i "s#^url = .*#url = \"http://127.0.0.1:$(cat "$LOG/port.txt")/wallet/payout\"#" "$CFG"
RUST_LOG=info "$BIN" "$CFG" --once > "$LOG/killed.log" 2>&1 & SIDECAR=$!
for i in $(seq 120); do [ -s "$LOG/held-raw.txt" ] && break; kill -0 "$SIDECAR" 2>/dev/null || exit 2; sleep 1; done
[ -s "$LOG/held-raw.txt" ] || exit 2
kill -KILL "$SIDECAR"; RC=0; wait "$SIDECAR" 2>/dev/null || RC=$?; SIDECAR=
check "sidecar actually killed with SIGKILL" eq "$RC" 137
kill "$PROXY"; wait "$PROXY" 2>/dev/null; PROXY=
cp "$LOG/direct.toml" "$CFG"
TX=$(payout_txid "$XA"); RAW=$(journal_raw "$XA")
check "batch journal fsynced before the intercepted send" eq "$(cat "$LOG/held-raw.txt")" "$RAW"
check "both coins share the signed transaction" eq "$(payout_txid "$XB")" "$TX"
check "killed transaction never reached Core" test -z "$(btc getrawtransaction "$TX" 2>/dev/null)"
[ -n "$TX" ] && [ -n "$RAW" ] || exit 2
(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
STOPPED=1
restore_claims() {
	(cd "$R" && docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error -d bark-server-db) < "$LOG/claimed.dump" > "$LOG/restore.log" 2>&1 || return 1
	eq "$(payout_state "$XA")|$(payout_state "$XB")|$(payout_txid "$XA")$(payout_txid "$XB")" 'claimed|claimed|'
}
restore_claims || exit 2

# A complete legacy record without bytes cannot authorize a replacement.
awk -v tx="$TX" '$2 == tx { print $1, $2; next } { print }' "$JOURNAL" > "$LOG/missing-raw.journal"
sed -i "s#^journal_path = .*#journal_path = \"$LOG/missing-raw.journal\"#" "$CFG"
RC=0; tick || RC=$?
check "missing raw transaction stops recovery" test "$RC" -ne 0
check "missing raw diagnostic names the payment" grep -q "$TX" "$LOG/tick.log"
check "missing raw diagnostic names a batch coin" grep -qE "$XA|$XB" "$LOG/tick.log"
cp "$LOG/direct.toml" "$CFG"

# Hold the last claimed row. Before the fix, the first row commits alone;
# after the fix both changes remain invisible until the batch can commit.
FIRST=$(q "SELECT vtxo_id FROM sidecar.payout WHERE vtxo_id IN ('$XA','$XB') ORDER BY claimed_at LIMIT 1")
LAST=$XA; [ "$FIRST" != "$XA" ] || LAST=$XB
q "SET application_name='restore-kill-lock-$RUN'; BEGIN;
	SELECT vtxo_id FROM sidecar.payout WHERE vtxo_id='$LAST' FOR UPDATE;
	SELECT pg_sleep(15); COMMIT" > "$LOG/row-lock.log" 2>&1 & LOCKER=$!
for i in $(seq 30); do
	[ "$(q "SELECT count(*) FROM pg_stat_activity WHERE application_name='restore-kill-lock-$RUN' AND wait_event='PgSleep'")" = 1 ] && break
	sleep 0.2
done
eq "$(q "SELECT count(*) FROM pg_stat_activity WHERE application_name='restore-kill-lock-$RUN' AND wait_event='PgSleep'")" 1 || exit 2
RUST_LOG=info "$BIN" "$CFG" --once > "$LOG/reattach.log" 2>&1 & SIDECAR=$!
for i in $(seq 40); do
	[ "$(q "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%UPDATE sidecar.payout%'")" -gt 0 ] && break
	sleep 0.2
done
check "recovery reached the held row" test "$(q "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%UPDATE sidecar.payout%'")" -gt 0
check "no batch member commits ahead of the held row" eq "$(payout_state "$FIRST")" claimed
wait "$LOCKER" || exit 2; LOCKER=
RC=0; wait "$SIDECAR" || RC=$?; SIDECAR=
check "recovery completes after releasing the row" eq "$RC" 0
check "A reattached to original transaction" eq "$(payout_txid "$XA")" "$TX"
check "B reattached to original transaction" eq "$(payout_txid "$XB")" "$TX"
check "original bytes broadcast after recovery" test -n "$(btc getmempoolentry "$TX" 2>/dev/null)"
(cd "$R" && docker compose start captaind watchmand > /dev/null) || exit 2
STOPPED=0
healthy captaind || exit 2
confirm_payouts
check "both recovered claims confirmed" eq "$(payout_state "$XA")|$(payout_state "$XB")" 'confirmed|confirmed'

(cd "$R" && docker compose stop captaind watchmand > /dev/null) || exit 2
STOPPED=1
restore_claims || exit 2
check "already-mined transaction reconciles without failure" tick
check "already-mined claims confirmed immediately" eq "$(payout_state "$XA")|$(payout_state "$XB")" 'confirmed|confirmed'
check "A received only one transaction" eq "$(wallet_sends_to "$(tr_address "$PKA")")" 1
check "B received only one transaction" eq "$(wallet_sends_to "$(tr_address "$PKB")")" 1
check "A retains original journal bytes" eq "$(journal_raw "$XA")" "$RAW"
check "B retains original journal bytes" eq "$(journal_raw "$XB")" "$RAW"
(cd "$R" && docker compose start captaind watchmand > /dev/null) || exit 2
STOPPED=0
healthy captaind || exit 2
expired_coin 60000
check "unrelated new coin still pays" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
