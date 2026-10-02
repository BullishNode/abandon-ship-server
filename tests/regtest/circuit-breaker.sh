#!/bin/bash
# A5: many quarantines in one tick smell like an encoding/schema change. With
# max_quarantine_per_tick=1 and three undecodable coins the sidecar stops
# after the first quarantine instead of quarantining everything.
. "$(dirname "$0")/lib.sh"
mkcfg max_quarantine_per_tick=1
WS=(); for n in a b c; do WS+=("$(wname $n)"); newwallet "${WS[-1]}"; done
round 60000 "${WS[@]}"
IDS=(); for w in "${WS[@]}"; do IDS+=("$(coins "$w")"); done
expire_and_sweep "${IDS[*]}" || finish
declare -A ORIG
for x in "${IDS[@]}"; do
	ORIG[$x]=$(q "SELECT encode(vtxo, 'hex') FROM vtxo WHERE vtxo_id='$x'")
	q "UPDATE vtxo SET vtxo='\\xdeadbeef'::bytea, updated_at=NOW() WHERE vtxo_id='$x'" > /dev/null
done
IN="'${IDS[0]}','${IDS[1]}','${IDS[2]}'"
tick; RC=$?
check "sidecar stops (exit $RC)" test $RC -ne 0
check "reason: circuit breaker" grep -q "more than 1 quarantines in one tick" "$LOG/tick.log"
check "at most one quarantined" test "$(q "SELECT count(*) FROM sidecar.quarantine WHERE vtxo_id IN ($IN)")" -le 1
check "nothing paid" eq "$(q "SELECT count(*) FROM sidecar.payout WHERE vtxo_id IN ($IN)")" 0
for x in "${IDS[@]}"; do q "UPDATE vtxo SET vtxo=decode('${ORIG[$x]}', 'hex'), updated_at=NOW() WHERE vtxo_id='$x'" > /dev/null; done
mkcfg
NQ=$(q "SELECT count(*) FROM sidecar.quarantine WHERE vtxo_id IN ($IN)")
check "blobs restored: the rest are paid" pay_until "$(for x in "${IDS[@]}"; do [ -z "$(quarantine_reason "$x")" ] && echo "$x"; done)" 3
check "paid count = 3 - quarantined" eq "$(q "SELECT count(*) FROM sidecar.payout WHERE vtxo_id IN ($IN)")" $((3 - NQ))
confirm_payouts
finish
