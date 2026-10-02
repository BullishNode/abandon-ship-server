#!/bin/bash
# The sidecar bans a coin, then is down longer than ban_blocks: the ban
# lapses. Back up, it bans again and waits the full ban wait before claiming;
# the coin is paid once.
. "$(dirname "$0")/lib.sh"
mkcfg ban_blocks=3
W=$(wname a); newwallet "$W"
round 60000 "$W"
X=$(coins "$W"); read -r PK AMT <<< "$(coininfo "$W" "$X")"
expire_and_sweep "$X" || finish

tick
U1=$(q "SELECT until_height FROM sidecar.ban WHERE vtxo_id='$X'")
check "banned" test -n "$U1"
mine 4
check "ban lapsed while the sidecar was down" test "${U1:-0}" -le "$(tip)"
tick
check "re-banned past the tip" test "$(q "SELECT until_height FROM sidecar.ban WHERE vtxo_id='$X'")" -gt "$(tip)"
check "not claimed on the lapsed ban" eq "$(payout_state "$X")" ""
check "paid after the new wait" pay_until "$X" 3
assert_paid "$X" "$PK" "$AMT"
confirm_payouts
finish
