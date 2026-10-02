#!/bin/bash
# X2/X8: T's exit (an arkoor coin, two steps deep) is started, its first
# step confirms, then it is cancelled before the final tx. At expiry the rest
# is swept. T's coin is partially unrolled: quarantined (Bull holds its value
# for manual review), never paid.
. "$(dirname "$0")/lib.sh"
mkcfg
S=$(wname sender); T=$(wname exiter); newwallet "$S"; newwallet "$T"
round 100000 "$S"
bark "$S" send "$(arkaddr "$T")" "40000 sat" > "$LOG/send.log" || say "send failed"
mine 1
X=$(coins "$T"); ANCHOR=$(q "SELECT anchor_point FROM vtxo WHERE vtxo_id='$X'")
say "coin $X, exit depth $(vtxo_field "$T" "$X" exit_depth)"
bark "$T" exit start --vtxo "$X" > "$LOG/exit-start.log" 2>&1 || say "exit start failed"
for i in $(seq 10); do
	bark "$T" exit progress >> "$LOG/exit-progress.log" 2>&1; mine 1; sleep 3
	[ -n "$(q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$ANCHOR' AND onchain_spent_txid IS NOT NULL")" ] && break
done
"$R/bark" "$T" exit cancel "$X" > "$LOG/exit-cancel.log" 2>&1
mine 1
check "first step on-chain (funding output spent)" test -n "$(q "SELECT onchain_spent_txid FROM vtxo WHERE vtxo_id='$ANCHOR' AND onchain_spent_txid IS NOT NULL")"
check "leaf never confirmed" test -z "$(q "SELECT confirmed_height FROM vtxo WHERE vtxo_id='$X' AND confirmed_height IS NOT NULL")"
expire_and_sweep "$X" || finish
for i in 1 2 3; do tick; sleep $(( $(ban_wait) + 1 )); done
check "quarantined: round partially unrolled" grep -q "round partially unrolled" <<< "$(quarantine_reason "$X")"
check "never paid" eq "$(payout_state "$X")" ""
check "leaf still unconfirmed" test -z "$(q "SELECT confirmed_height FROM vtxo WHERE vtxo_id='$X' AND confirmed_height IS NOT NULL")"
finish
