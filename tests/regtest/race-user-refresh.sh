#!/bin/bash
# A2 + #15/#56/A17: owners of expired, swept coins come back.
# - A refreshes before the sidecar bans: refresh works, A's coin is never paid.
# - B refreshes during the ban wait: refused, then paid on-chain (O19).
# - C submits a delegated refresh during the ban wait: refused, then paid.
# ban_wait_secs=45: above a round (30 s interval + 5 s submit + 5 s sign).
. "$(dirname "$0")/lib.sh"
mkcfg ban_wait_secs=45
A=$(wname a); B=$(wname b); C=$(wname c)
for w in $A $B $C; do newwallet "$w"; done
round 60000 "$A" "$B" "$C"
XA=$(coins "$A"); XB=$(coins "$B"); XC=$(coins "$C")
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"; read -r PKC AMTC <<< "$(coininfo "$C" "$XC")"
expire_and_sweep "$XA $XB $XC" || finish

bark "$A" refresh --vtxo "$XA" > "$LOG/refresh-a.log" 2>&1
mine 2
check "A's refresh after the sweep works" test -n "$(q "SELECT spent_in_round FROM vtxo WHERE vtxo_id='$XA' AND spent_in_round IS NOT NULL")"
check "A holds a new spendable coin" test -n "$(coins "$A")"

tick
check "B and C banned" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id IN ('$XB','$XC')")" 2
check "A not banned" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id='$XA'")" 0
"$R/bark" "$B" refresh --vtxo "$XB" > "$LOG/refresh-b.log" 2>&1; RB=$?
"$R/bark" "$C" refresh --delegated --vtxo "$XC" > "$LOG/refresh-c.log" 2>&1; RC=$?
mine 1
check "B told 'unusable inputs'" grep -q "unusable inputs: \\[$XB\\]" "$LOG/refresh-b.log"
check "C told 'unusable inputs'" grep -q "unusable inputs: \\[$XC\\]" "$LOG/refresh-c.log"
check "B's refresh refused during the wait (exit $RB)" test -z "$(q "SELECT spent_in_round FROM vtxo WHERE vtxo_id='$XB' AND spent_in_round IS NOT NULL")"
check "C's delegated refresh refused during the wait (exit $RC)" test -z "$(q "SELECT 1 FROM round_part_input WHERE vtxo_id='$XC'")"

check "B and C paid" pay_until "$XB $XC" 3
assert_paid "$XB" "$PKB" "$AMTB"
assert_paid "$XC" "$PKC" "$AMTC"
check "A never paid" eq "$(payout_state "$XA")" ""
confirm_payouts
finish
