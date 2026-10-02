#!/bin/bash
# #52/A11: captaind's spend of a coin is open (row lock held) when the claim
# runs. The claim waits for the lock: if the spend commits, the claim loses
# (no payout); if it rolls back, the claim wins (paid).
. "$(dirname "$0")/lib.sh"
mkcfg
WC=$(wname commit); WR=$(wname rollback); newwallet "$WC"; newwallet "$WR"
round 60000 "$WC" "$WR"
X=$(coins "$WC"); Z=$(coins "$WR"); read -r PKZ AMTZ <<< "$(coininfo "$WR" "$Z")"
expire_and_sweep "$X $Z" || finish

tick; check "both banned" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id IN ('$X','$Z')")" 2
sleep $(( $(ban_wait) + 1 ))
# Stand-in for captaind's conditional spend, held open for 12 s.
hold() { "$R/psql" -c "BEGIN; UPDATE vtxo SET spend_state='spent', updated_at=NOW() WHERE vtxo_id='$1' AND spend_state='spendable'; SELECT pg_sleep(12); $2;" > "$LOG/hold-$2.log" 2>&1; }
hold "$X" COMMIT & H1=$!
hold "$Z" ROLLBACK & H2=$!
sleep 2
T0=$(date +%s); tick; T1=$(date +%s); wait $H1 $H2
check "claim tick blocked on the row locks ($((T1 - T0)) s)" test $((T1 - T0)) -ge 8
check "commit side: claim lost" grep -q "user redeemed first; skipped vtxo=$X" "$LOG/tick.log"
check "commit side: no payout row" eq "$(payout_state "$X")" ""
check "commit side: coin spent by the other writer" eq "$(spend_state "$X")" spent
check "rollback side: claimed and paid" pay_until "$Z" 1
assert_paid "$Z" "$PKZ" "$AMTZ"
confirm_payouts
finish
