#!/bin/bash
# E starts an exit with no on-chain funds for fees: nothing confirms. The
# coin expires, the round is swept wholesale, and the coin is paid on-chain.
. "$(dirname "$0")/lib.sh"
mkcfg
S=$(wname sender); E=$(wname broke); newwallet "$S"; newwallet "$E" 0
round 100000 "$S"
bark "$S" send "$(arkaddr "$E")" "50000 sat" > "$LOG/send.log" || say "send failed"
X=$(coins "$E"); read -r PK AMT <<< "$(coininfo "$E" "$X")"; ANCHOR=$(anchor_of "$X")
"$R/bark" "$E" exit start --vtxo "$X" > "$LOG/exit-start.log" 2>&1 || say "exit start failed"
bark "$E" exit list > "$LOG/exit-list.log"
for i in 1 2 3; do "$R/bark" "$E" exit progress >> "$LOG/exit-progress.log" 2>&1; mine 1; sleep 2; done
check "exit started (listed)" grep -q "${X%:*}" "$LOG/exit-list.log"
check "progress cannot pay fees" grep -qiE "insufficient|not enough|fund" "$LOG/exit-progress.log"
check "exit stuck: funding output unspent before expiry" test -z "$(anchor_spender "$ANCHOR")"
expire_and_sweep "$X" || finish
check "swept wholesale (spender is a sweep, not a tree tx)" test -z "$(q "SELECT 1 FROM vtxo WHERE vtxo_txid='$(anchor_spender "$ANCHOR")'")"
check "paid" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
