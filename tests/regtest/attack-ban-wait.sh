#!/bin/bash
# Owners try to move expired, swept coins out of Ark around the payout.
# - A arkoor-sends part of its coin to D before the sidecar acts: if captaind
#   cosigns, the children are paid, the parent never, and never more than A had.
# - During the ban wait B arkoor-sends and C offboards: both refused, both paid once.
. "$(dirname "$0")/lib.sh"
mkcfg ban_wait_secs=60
A=$(wname a); B=$(wname b); C=$(wname c); D=$(wname d)
for w in $A $B $C; do newwallet "$w"; done; newwallet "$D" 0
round 60000 "$A" "$B" "$C"
XA=$(coins "$A"); XB=$(coins "$B"); XC=$(coins "$C")
read -r _ AMTA <<< "$(coininfo "$A" "$XA")"
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"; read -r PKC AMTC <<< "$(coininfo "$C" "$XC")"
expire_and_sweep "$XA $XB $XC" || finish
DADDR=$(arkaddr "$D")

"$R/bark" "$A" send "$DADDR" "30000 sat" > "$LOG/send-a.log" 2>&1; say "A's arkoor send of an expired coin exited $?"

tick
check "B and C banned" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id IN ('$XB','$XC')")" 2
timeout 50 "$R/bark" "$B" send "$DADDR" "30000 sat" > "$LOG/send-b.log" 2>&1; say "B's send exited $?"
timeout 50 "$R/bark" "$C" offboard --vtxo "$XC" > "$LOG/offboard-c.log" 2>&1; say "C's offboard exited $?"
mine 1
check "B's arkoor send refused during the wait" test -z "$(q "SELECT oor_spent_txid FROM vtxo WHERE vtxo_id='$XB' AND oor_spent_txid IS NOT NULL")"
check "C's offboard refused during the wait" test -z "$(q "SELECT offboarded_in FROM vtxo WHERE vtxo_id='$XC' AND offboarded_in IS NOT NULL")"

check "B and C paid" pay_until "$XB $XC" 3
assert_paid "$XB" "$PKB" "$AMTB"
assert_paid "$XC" "$PKC" "$AMTC"

# A's coin and whatever came out of it (A's change, D's coin).
KIDS=$(q "SELECT vtxo_id FROM vtxo WHERE vtxo_id <> '$XA' AND vtxo_id IN ($(for w in $A $D; do for i in $(coins "$w") $(coins "$w" spent); do printf "'%s'," "$i"; done; done)'')")
[ -n "$KIDS" ] && pay_until "$KIDS" 3
if [ -n "$(q "SELECT 1 FROM vtxo WHERE vtxo_id='$XA' AND oor_spent_txid IS NOT NULL")" ]; then
	say "A's send was cosigned; children: $(echo $KIDS)"
	check "A's spent parent never paid" eq "$(payout_state "$XA")" ""
else
	check "A paid" pay_until "$XA" 3
fi
PAIDA=$(q "SELECT coalesce(sum(amount_sat),0) FROM sidecar.payout WHERE vtxo_id IN ('$XA',$(for i in $KIDS; do printf "'%s'," "$i"; done)'')")
check "paid for A's coin and its children ($PAIDA) <= $AMTA" test "$PAIDA" -le "$AMTA"
confirm_payouts
finish
