#!/bin/bash
# A exits unilaterally before expiry and its leaf confirms; B
# shares A's round. A is never paid (confirmed_height). B's round is
# partially unrolled, so B is quarantined (manual review), never paid.
. "$(dirname "$0")/lib.sh"
mkcfg
A=$(wname a); B=$(wname b); newwallet "$A"; newwallet "$B"
round 60000 "$A" "$B"
XA=$(coins "$A"); XB=$(coins "$B")
bark "$A" exit start --vtxo "$XA" > "$LOG/exit-start.log" 2>&1 || say "exit start failed"
for i in $(seq 30); do
	[ -n "$(leaf_confirmed "$XA")" ] && break
	bark "$A" exit progress >> "$LOG/exit-progress.log" 2>&1; mine 1; sleep 2
done
check "A's leaf confirmed (exit done)" test -n "$(leaf_confirmed "$XA")"
expire_and_sweep "$XA $XB" || finish
ticks 3
check "A never paid" eq "$(payout_state "$XA")" ""
check "A never banned" eq "$(bans "$XA")" 0
check "B quarantined: round partially unrolled" grep -q "round partially unrolled" <<< "$(quarantine_reason "$XB")"
check "B never paid" eq "$(payout_state "$XB")" ""
finish
