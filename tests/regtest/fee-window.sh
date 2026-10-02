#!/bin/bash
# Coins unaffordable at today's fee rate must not fill the candidate window
# (max_batch x 20, oldest expiry first). 21 arkoor sends of 2000 sat leave
# more than 20 coins of 1000-2999 sat (unaffordable at 20% above 2.6 sat/vB)
# that expire first, and a big coin later; with max_batch=1 the big coin is
# still paid. The sender's change coins, after 21 arkoor sends, are paid too.
. "$(dirname "$0")/lib.sh"
S=$(wname sender); T=$(wname small); B=$(wname big)
newwallet "$S"; newwallet "$T" 0; newwallet "$B"
round 200000 "$S"
for i in $(seq 21); do bark "$S" send "$(arkaddr "$T")" "2000 sat" >> "$LOG/send.log" || say "send $i failed"; done
round 500000 "$B"
XS=$(coins "$T"); XB=$(coins "$B"); XC=$(coins "$S")
X=$(echo $XS | sed "s/ /','/g")
read -r PKB AMTB <<< "$(coininfo "$B" "$XB")"
INFO=(); for c in $XC; do INFO+=("$c $(coininfo "$S" "$c")"); done
check "more than 20 coins of 1000-2999 sat" test "$(q "SELECT count(*) FROM vtxo WHERE vtxo_id IN ('$X') AND amount BETWEEN 1000 AND 2999")" -gt 20
check "small coins expire before the big one" test "$(q "SELECT max(expiry) FROM vtxo WHERE vtxo_id IN ('$X')")" -lt "$(q "SELECT expiry FROM vtxo WHERE vtxo_id='$XB'")"
expire_and_sweep "$XS $XB $XC" || finish

mkcfg max_batch=1
check "big coin and change coins paid" pay_until "$XB $XC" 12
assert_paid "$XB" "$PKB" "$AMTB"
for i in "${INFO[@]}"; do assert_paid $i; done
check "small coins untouched" eq "$(q "SELECT count(*) FROM sidecar.ban WHERE vtxo_id IN ('$X')")" 0
confirm_payouts
finish
