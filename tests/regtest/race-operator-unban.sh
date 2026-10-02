#!/bin/bash
# The claim needs the sidecar's own ban, still in place.
# - operator unbans during the wait: the wait restarts, no claim;
# - operator re-bans with another (longer) height: no claim while it holds;
# - our ban lapses (blocks mined past it): the wait restarts, no claim;
# - operator unbans while the claim waits on the coin's row lock: claim lost;
# - with our ban intact and aged: claimed and paid.
. "$(dirname "$0")/lib.sh"
mkcfg
expired_coin 60000
WAIT=$(( $(ban_wait) + 1 ))
banned_at() { q "SELECT banned_at FROM sidecar.ban WHERE vtxo_id='$X'"; }
no_claim() { check "$1: no claim" eq "$(payout_state "$X")" ""; check "$1: coin spendable" eq "$(spend_state "$X")" spendable; }

tick; check "banned" test -n "$(banned_at)"; T1=$(banned_at)
q "UPDATE vtxo SET banned_until_height=NULL , updated_at=NOW() WHERE vtxo_id='$X'" > /dev/null
sleep $WAIT; tick
no_claim "operator unban"
check "operator unban: re-banned, wait restarted" test "$(banned_at)" != "$T1"

sleep $WAIT
LONG=$(( $(tip) + 500 ))
q "UPDATE vtxo SET banned_until_height=$LONG , updated_at=NOW() WHERE vtxo_id='$X'" > /dev/null
tick; sleep $WAIT; tick
no_claim "operator's longer ban"
check "operator's ban not shortened" eq "$(q "SELECT banned_until_height FROM vtxo WHERE vtxo_id='$X'")" "$LONG"

q "UPDATE vtxo SET banned_until_height=NULL , updated_at=NOW() WHERE vtxo_id='$X'" > /dev/null
tick; T2=$(banned_at)
UNTIL=$(q "SELECT until_height FROM sidecar.ban WHERE vtxo_id='$X'")
mine $(( UNTIL - $(tip) )); ensure_fees
sleep $WAIT; tick
no_claim "our ban lapsed"
check "lapsed: re-banned, wait restarted" test "$(banned_at)" != "$T2"

sleep $WAIT
"$R/psql" -c "BEGIN; UPDATE vtxo SET banned_until_height=NULL, updated_at=NOW() WHERE vtxo_id='$X'; SELECT pg_sleep(10); COMMIT;" > "$LOG/hold.log" 2>&1 & H=$!
sleep 2; tick; wait $H
check "unban while the claim waits: claim lost" grep -q "user redeemed first; skipped vtxo=$X" "$LOG/tick.log"
no_claim "unban while the claim waits"

check "intact ban: paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
