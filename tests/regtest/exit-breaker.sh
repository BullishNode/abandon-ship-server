#!/bin/bash
# One owner's partial exit unrolls a round: every remaining coin of that round
# is quarantined. That is one on-chain fact, not many failures: it must not
# trip the circuit breaker (max_quarantine_per_tick=1) and stop payouts of
# other rounds (Z).
. "$(dirname "$0")/lib.sh"
mkcfg max_quarantine_per_tick=1
A=$(wname a); B=$(wname b); C=$(wname c); T=$(wname exiter); Z=$(wname z)
for w in $A $B $C $T $Z; do newwallet "$w"; done
round 60000 "$A" "$B" "$C"
bark "$A" send "$(arkaddr "$T")" "20000 sat" > "$LOG/send.log" || say "send failed"
mine 1
X=$(coins "$T"); ANCHOR=$(anchor_of "$X")
bark "$T" exit start --vtxo "$X" > "$LOG/exit-start.log" 2>&1 || say "exit start failed"
for i in $(seq 10); do
	bark "$T" exit progress >> "$LOG/exit-progress.log" 2>&1; mine 1; sleep 3
	[ -n "$(anchor_spender "$ANCHOR")" ] && break
done
"$R/bark" "$T" exit cancel "$X" > "$LOG/exit-cancel.log" 2>&1
round 60000 "$Z"
XA=$(coins "$A"); XB=$(coins "$B"); XC=$(coins "$C"); XZ=$(coins "$Z")
read -r PKZ AMTZ <<< "$(coininfo "$Z" "$XZ")"
check "round unrolled (funding output spent)" test -n "$(anchor_spender "$ANCHOR")"
expire_and_sweep "$XA $XB $XC $XZ" || finish

# Every user coin left in the unrolled round (a wallet whose refresh missed
# the round keeps a board coin elsewhere: not part of it).
UNR=$(q "SELECT vtxo_id FROM vtxo WHERE anchor_point='$ANCHOR' AND policy_type='pubkey' AND spend_state='spendable'")
check "two or more coins left in the unrolled round" test "$(wc -w <<< "$UNR")" -ge 2
tick; RC=$?
check "breaker not tripped (exit $RC)" test $RC -eq 0
for x in $UNR; do
	check "${x:0:8} quarantined: round partially unrolled" grep -q "round partially unrolled" <<< "$(quarantine_reason "$x")"
done
check "Z paid" pay_until "$XZ" 3
assert_paid "$XZ" "$PKZ" "$AMTZ"
check "unrolled round never paid" eq "$(q "SELECT count(*) FROM sidecar.payout p JOIN vtxo v USING (vtxo_id) WHERE v.anchor_point='$ANCHOR'")" 0
confirm_payouts
finish
